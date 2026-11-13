import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sakila_sync.models.analytics import (
    BridgeFilmActor, BridgeFilmCategory, DimActor, DimCategory, DimCustomer,
    DimDate, DimFilm, DimStore, FactPayment, FactRental
)
from sakila_sync.models.sakila import (
    Actor, Category, Customer, Film, FilmActor, FilmCategory, Inventory,
    Payment, Rental, Staff, Store
)
from sakila_sync.sync.full_load import date_key

TOLERANCE = Decimal("0.01")
DEFAULT_WINDOW_DAYS = 30
TABLE_COUNTS = [
    ("film", Film, DimFilm), ("actor", Actor, DimActor),
    ("category", Category, DimCategory), ("store", Store, DimStore),
    ("customer", Customer, DimCustomer), ("film_actor", FilmActor, BridgeFilmActor),
    ("film_category", FilmCategory, BridgeFilmCategory),
]
NATURAL_KEYS = [
    (DimFilm, DimFilm.film_id), (DimActor, DimActor.actor_id),
    (DimCategory, DimCategory.category_id), (DimStore, DimStore.store_id),
    (DimCustomer, DimCustomer.customer_id),
    (FactRental, FactRental.rental_id), (FactPayment, FactPayment.payment_id)
]
FACT_LINKS = [
    (FactRental, FactRental.film_key, DimFilm.film_key),
    (FactRental, FactRental.store_key, DimStore.store_key),
    (FactRental, FactRental.customer_key, DimCustomer.customer_key),
    (FactRental, FactRental.date_key_rented, DimDate.date_key),
    (FactRental, FactRental.date_key_returned, DimDate.date_key),
    (FactPayment, FactPayment.customer_key, DimCustomer.customer_key),
    (FactPayment, FactPayment.store_key, DimStore.store_key),
    (FactPayment, FactPayment.date_key_paid, DimDate.date_key),
]

log = logging.getLogger("sakila_sync.validate")

def resolve_period(src: Session, date_from: date | None, date_to: date | None) -> tuple[date, date]:
    if date_to is None:
        latest = src.scalar(select(func.max(Payment.payment_date)))
        if latest is None:
            raise RuntimeError("source has no payments, cannot choose a default period")
        date_to = latest.date()
    if date_from is None:
        date_from = date_to - timedelta(days=DEFAULT_WINDOW_DAYS - 1)
    if date_from > date_to:
        raise ValueError(f"--from {date_from} is after --to {date_to}")
    return date_from, date_to

def _compare(label: str, source: dict, target: dict, errors: list[str], tol: Decimal | None = None) -> None:
    for key in sorted(set(source) | set(target)):
        s, t = source.get(key, 0), target.get(key, 0)
        bad = abs(Decimal(s) - Decimal(t)) > tol if tol is not None else s != t
        if bad:
            errors.append(f"{label} {key}: source={s} target={t}")

def _check_table_counts(src, dst, errors) -> None:
    for label, s_model, t_model in TABLE_COUNTS:
        s = src.scalar(select(func.count()).select_from(s_model))
        t = dst.scalar(select(func.count()).select_from(t_model))
        if s != t:
            errors.append(f"row count {label}: source={s} target={t}")

def _check_period(src, dst, start: date, end: date, errors) -> None:
    dt_from = datetime.combine(start, datetime.min.time())
    dt_to = datetime.combine(end + timedelta(days=1), datetime.min.time())
    k_from, k_to = date_key(start), date_key(end)

    rent_in = (Rental.rental_date >= dt_from, Rental.rental_date < dt_to)
    pay_in = (Payment.payment_date >= dt_from, Payment.payment_date < dt_to)
    frent_in = FactRental.date_key_rented.between(k_from, k_to)
    fpay_in = FactPayment.date_key_paid.between(k_from, k_to)

    s_total = src.scalar(select(func.count()).select_from(Rental).where(*rent_in))
    t_total = dst.scalar(select(func.count()).select_from(FactRental).where(frent_in))
    if s_total != t_total:
        errors.append(f"rentals in period: source={s_total} target={t_total}")

    s_rent = dict(src.execute(
        select(Inventory.store_id, func.count())
        .select_from(Rental).join(Inventory, Rental.inventory_id == Inventory.inventory_id)
        .where(*rent_in).group_by(Inventory.store_id)).all())
    t_rent = dict(dst.execute(
        select(DimStore.store_id, func.count())
        .select_from(FactRental).join(DimStore, FactRental.store_key == DimStore.store_key)
        .where(frent_in).group_by(DimStore.store_id)).all())
    _compare("rentals per store", s_rent, t_rent, errors)

    s_ids = set(src.scalars(select(Rental.rental_id).where(*rent_in)))
    t_ids = set(dst.scalars(select(FactRental.rental_id).where(frent_in)))
    _report_ids("rental_id", s_ids, t_ids, errors)

    s_total = src.scalar(select(func.count()).select_from(Payment).where(*pay_in))
    t_total = dst.scalar(select(func.count()).select_from(FactPayment).where(fpay_in))
    if s_total != t_total:
        errors.append(f"payments in period: source={s_total} target={t_total}")

    store_expr = func.coalesce(Inventory.store_id, Staff.store_id)
    s_amt = dict(src.execute(
        select(store_expr, func.sum(Payment.amount))
        .select_from(Payment)
        .join(Staff, Payment.staff_id == Staff.staff_id)
        .outerjoin(Rental, Payment.rental_id == Rental.rental_id)
        .outerjoin(Inventory, Rental.inventory_id == Inventory.inventory_id)
        .where(*pay_in).group_by(store_expr)).all())
    t_amt = dict(dst.execute(
        select(DimStore.store_id, func.sum(FactPayment.amount))
        .select_from(FactPayment).join(DimStore, FactPayment.store_key == DimStore.store_key)
        .where(fpay_in).group_by(DimStore.store_id)).all())
    _compare("payment amount per store", s_amt, t_amt, errors, tol=TOLERANCE)

    s_ids = set(src.scalars(select(Payment.payment_id).where(*pay_in)))
    t_ids = set(dst.scalars(select(FactPayment.payment_id).where(fpay_in)))
    _report_ids("payment_id", s_ids, t_ids, errors)

def _report_ids(name: str, source: set[int], target: set[int], errors: list[str]) -> None:
    missing, extra = sorted(source - target), sorted(target - source)
    if missing:
        errors.append(f"{len(missing)} {name}(s) missing in target, e.g. {missing[:5]}")
    if extra:
        errors.append(f"{len(extra)} {name}(s) in target but not in source, e.g. {extra[:5]}")

def _check_integrity(dst, errors) -> None:
    for model, col in NATURAL_KEYS:
        dupes = dst.execute(select(col, func.count()).group_by(col).having(func.count() > 1)).all()
        if dupes:
            errors.append(f"duplicate {col.key} in {model.__tablename__}: {[d[0] for d in dupes[:5]]}")

    for fact, fk, dim_pk in FACT_LINKS:
        orphans = dst.scalar(
            select(func.count()).select_from(fact)
            .outerjoin(dim_pk.class_, fk == dim_pk)
            .where(fk.is_not(None), dim_pk.is_(None)))
        if orphans:
            errors.append(f"{orphans} {fact.__tablename__} row(s) with no matching row for {fk.key}")

def validate(src: Session, dst: Session, date_from: date | None = None,
             date_to: date | None = None) -> list[str]:
    """Run all checks, log and return the list of problems"""
    start, end = resolve_period(src, date_from, date_to)
    log.info("validating period %s .. %s", start, end)

    errors: list[str] = []
    _check_table_counts(src, dst, errors)
    _check_period(src, dst, start, end, errors)
    _check_integrity(dst, errors)

    for e in errors:
        log.error("MISMATCH: %s", e)
    if errors:
        log.error("validation FAILED with %d problem(s)", len(errors))
    else:
        log.info("validation passed: counts, totals and keys all match")
    return errors
