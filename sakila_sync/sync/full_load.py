import logging
from datetime import date, datetime
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from sakila_sync.models.analytics import (
    BridgeFilmActor, BridgeFilmCategory, DimActor, DimCategory, DimCustomer,
    DimDate, DimFilm, DimStore, FactPayment, FactRental, SyncState
)
from sakila_sync.models.sakila import (
    Actor, Address, Category, City, Country, Customer, Film, FilmActor,
    FilmCategory, Inventory, Language, Payment, Rental, Staff, Store
)

SOURCE_MODELS = [
    Film, Language, Actor, Category, FilmActor, FilmCategory, Inventory,
    Rental, Payment, Store, Staff, Customer, Address, City, Country
]

log = logging.getLogger("sakila_sync.full_load")

def date_key(d: date | datetime | None) -> int | None:
    """YYYYMMDD integer key for a date/datetime (None stays None)."""
    return None if d is None else int(d.strftime("%Y%m%d"))

def make_dim_date(d: date) -> DimDate:
    dow = d.isoweekday()
    return DimDate(
        date_key=date_key(d), date=d, year=d.year,
        quarter=(d.month-1)//3 + 1, month=d.month, day_of_month=d.day,
        day_of_week=dow, is_weekend=1 if dow >= 6 else 0
    )

def _clear_target(dst: Session) -> None:
    """Delete in reverse load order (facts, bridges, dimensions)."""
    for model in (
        FactPayment, FactRental,
        BridgeFilmCategory, BridgeFilmActor,
        DimCustomer, DimStore,
        DimCategory, DimActor,
        DimFilm, DimDate,
    ):
        dst.execute(delete(model))

def _load_dim_date(src: Session, dst: Session) -> int:
    dates: set[date] = set()
    for col in (Rental.rental_date, Rental.return_date, Payment.payment_date):
        dates.update(v.date() for v in src.scalars(select(col).distinct()) if v)
    dst.add_all(make_dim_date(d) for d in sorted(dates))
    dst.flush()
    return len(dates)

def _load_dim_film(src: Session, dst: Session) -> dict[int, int]:
    rows = src.execute(
        select(Film, Language.name).join(Language, Film.language_id == Language.language_id)
    ).all()
    objs = [
        DimFilm(film_id=f.film_id, title=f.title, rating=f.rating, length=f.length,
                language=lang, release_year=f.release_year, last_update=f.last_update)
        for f, lang in rows
    ]
    dst.add_all(objs)
    dst.flush()
    return {o.film_id: o.film_key for o in objs}

def _load_dim_actor(src: Session, dst: Session) -> dict[int, int]:
    objs = [
        DimActor(actor_id=a.actor_id, first_name=a.first_name,
                 last_name=a.last_name, last_update=a.last_update)
        for a in src.scalars(select(Actor))
    ]
    dst.add_all(objs)
    dst.flush()
    return {o.actor_id: o.actor_key for o in objs}

def _load_dim_category(src: Session, dst: Session) -> dict[int, int]:
    objs = [
        DimCategory(category_id=c.category_id, name=c.name, last_update=c.last_update)
        for c in src.scalars(select(Category))
    ]
    dst.add_all(objs)
    dst.flush()
    return {o.category_id: o.category_key for o in objs}

def _load_dim_store(src: Session, dst: Session) -> dict[int, int]:
    rows = src.execute(
        select(Store, City.city, Country.country)
        .join(Address, Store.address_id == Address.address_id)
        .join(City, Address.city_id == City.city_id)
        .join(Country, City.country_id == Country.country_id)
    ).all()
    objs = [
        DimStore(store_id=s.store_id, city=city, country=country, last_update=s.last_update)
        for s, city, country in rows
    ]
    dst.add_all(objs)
    dst.flush()
    return {o.store_id: o.store_key for o in objs}

def _load_dim_customer(src: Session, dst: Session) -> dict[int, int]:
    rows = src.execute(
        select(Customer, City.city, Country.country)
        .join(Address, Customer.address_id == Address.address_id)
        .join(City, Address.city_id == City.city_id)
        .join(Country, City.country_id == Country.country_id)
    ).all()
    objs = [
        DimCustomer(customer_id=c.customer_id, first_name=c.first_name,
                    last_name=c.last_name, active=c.active, city=city, country=country,
                    last_update=c.last_update)
        for c, city, country in rows
    ]
    dst.add_all(objs)
    dst.flush()
    return {o.customer_id: o.customer_key for o in objs}

def _load_bridge_film_actor(src: Session, dst: Session, film_keys, actor_keys) -> int:
    objs = [
        BridgeFilmActor(film_key=film_keys[r.film_id], actor_key=actor_keys[r.actor_id])
        for r in src.scalars(select(FilmActor))
    ]
    dst.add_all(objs)
    dst.flush()
    return len(objs)

def _load_bridge_film_category(src: Session, dst: Session, film_keys, category_keys) -> int:
    objs = [
        BridgeFilmCategory(film_key=film_keys[r.film_id], category_key=category_keys[r.category_id])
        for r in src.scalars(select(FilmCategory))
    ]
    dst.add_all(objs)
    dst.flush()
    return len(objs)

def _load_fact_rental(src: Session, dst: Session, film_keys, store_keys, customer_keys) -> dict[int, int]:
    rows = src.execute(
        select(Rental, Inventory.film_id, Inventory.store_id)
        .join(Inventory, Rental.inventory_id == Inventory.inventory_id)
    ).all()
    objs = []
    rental_store: dict[int, int] = {}
    for r, film_id, store_id in rows:
        rental_store[r.rental_id] = store_id
        days = (r.return_date.date() - r.rental_date.date()).days if r.return_date else None
        objs.append(FactRental(
            rental_id=r.rental_id, date_key_rented=date_key(r.rental_date),
            date_key_returned=date_key(r.return_date), film_key=film_keys[film_id],
            store_key=store_keys[store_id], customer_key=customer_keys[r.customer_id],
            staff_id=r.staff_id, rental_duration_days=days
        ))
    dst.add_all(objs)
    dst.flush()
    return rental_store

def _load_fact_payment(src: Session, dst: Session, store_keys, customer_keys, rental_store) -> int:
    staff_store = {s.staff_id: s.store_id for s in src.scalars(select(Staff))}
    objs = []
    for p in src.scalars(select(Payment)):
        store_id = rental_store.get(p.rental_id) if p.rental_id else None
        if store_id is None:
            store_id = staff_store[p.staff_id]
        objs.append(FactPayment(
            payment_id=p.payment_id, date_key_paid=date_key(p.payment_date),
            customer_key=customer_keys[p.customer_id], store_key=store_keys[store_id],
            staff_id=p.staff_id, amount=p.amount
        ))
    dst.add_all(objs)
    dst.flush()
    return len(objs)

def _write_markers(src: Session, dst: Session) -> None:
    for model in SOURCE_MODELS:
        marker = src.scalar(select(func.max(model.last_update)))
        dst.merge(SyncState(source_table=model.__tablename__, last_update=marker))

def full_load(src: Session, dst: Session) -> dict[str, int]:
    """Run the full load in a single transaction"""
    counts: dict[str, int] = {}
    try:
        _clear_target(dst)

        counts["dim_date"] = _load_dim_date(src, dst)
        film_keys = _load_dim_film(src, dst)
        actor_keys = _load_dim_actor(src, dst)
        category_keys = _load_dim_category(src, dst)
        store_keys = _load_dim_store(src, dst)
        customer_keys = _load_dim_customer(src, dst)
        counts.update(dim_film=len(film_keys), dim_actor=len(actor_keys),
                      dim_category=len(category_keys), dim_store=len(store_keys),
                      dim_customer=len(customer_keys))

        counts["bridge_film_actor"] = _load_bridge_film_actor(src, dst, film_keys, actor_keys)
        counts["bridge_film_category"] = _load_bridge_film_category(src, dst, film_keys, category_keys)

        rental_store = _load_fact_rental(src, dst, film_keys, store_keys, customer_keys)
        counts["fact_rental"] = len(rental_store)
        counts["fact_payment"] = _load_fact_payment(src, dst, store_keys, customer_keys, rental_store)

        _write_markers(src, dst)
        dst.commit()
    except Exception:
        dst.rollback()
        log.exception("Full load failed. Analytics database rolled back.")
        raise

    for table, n in counts.items():
        log.info("loaded %-22s %d rows", table, n)
    return counts
