import logging
from collections import defaultdict
from datetime import date, datetime
from sqlalchemy import func, or_, select, true
from sqlalchemy.orm import Session

from sakila_sync.models.analytics import (
    BridgeFilmActor, BridgeFilmCategory, DimActor, DimCategory, DimCustomer,
    DimDate, DimFilm, DimStore, FactPayment, FactRental, SyncState
)
from sakila_sync.models.sakila import (
    Actor, Address, Category, City, Country, Customer, Film, FilmActor,
    FilmCategory, Inventory, Language, Payment, Rental, Staff, Store
)
from sakila_sync.sync.full_load import SOURCE_MODELS, date_key, make_dim_date

log = logging.getLogger("sakila_sync.incremental")

Stats = dict[str, dict[str, int]]

def _changed(col, marker: datetime | None):
    return true() if marker is None else col >= marker

def _upsert(dst: Session, model, key: str, values: dict, stats: Stats) -> None:
    name = model.__tablename__
    row = dst.scalar(select(model).where(getattr(model, key) == values[key]))
    if row is None:
        dst.add(model(**values))
        stats[name]["inserted"] += 1
        return
    dirty = False
    for attr, val in values.items():
        if getattr(row, attr) != val:
            setattr(row, attr, val)
            dirty = True
    if dirty:
        stats[name]["updated"] += 1

def _key_map(dst: Session, model, id_col: str, key_col: str) -> dict[int, int]:
    return dict(dst.execute(select(getattr(model, id_col), getattr(model, key_col))).all())

def _read_markers(dst: Session) -> dict[str, datetime | None]:
    markers = {s.source_table: s.last_update for s in dst.scalars(select(SyncState))}
    if not markers:
        raise RuntimeError("sync_state is empty")
    return {m.__tablename__: markers.get(m.__tablename__) for m in SOURCE_MODELS}

def _sync_dim_film(src, dst, mk, stats) -> None:
    rows = src.execute(
        select(Film, Language.name)
        .join(Language, Film.language_id == Language.language_id)
        .where(or_(_changed(Film.last_update, mk["film"]),
                   _changed(Language.last_update, mk["language"])))
    ).all()
    for f, lang in rows:
        _upsert(dst, DimFilm, "film_id", dict(
            film_id=f.film_id, title=f.title, rating=f.rating, length=f.length,
            language=lang, release_year=f.release_year, last_update=f.last_update), stats)

def _sync_dim_actor(src, dst, mk, stats) -> None:
    for a in src.scalars(select(Actor).where(_changed(Actor.last_update, mk["actor"]))):
        _upsert(dst, DimActor, "actor_id", dict(
            actor_id=a.actor_id, first_name=a.first_name,
            last_name=a.last_name, last_update=a.last_update), stats)

def _sync_dim_category(src, dst, mk, stats) -> None:
    for c in src.scalars(select(Category).where(_changed(Category.last_update, mk["category"]))):
        _upsert(dst, DimCategory, "category_id", dict(
            category_id=c.category_id, name=c.name, last_update=c.last_update), stats)

def _sync_dim_store(src, dst, mk, stats) -> None:
    rows = src.execute(
        select(Store, City.city, Country.country)
        .join(Address, Store.address_id == Address.address_id)
        .join(City, Address.city_id == City.city_id)
        .join(Country, City.country_id == Country.country_id)
        .where(or_(_changed(Store.last_update, mk["store"]),
                   _changed(Address.last_update, mk["address"]),
                   _changed(City.last_update, mk["city"]),
                   _changed(Country.last_update, mk["country"])))
    ).all()
    for s, city, country in rows:
        _upsert(dst, DimStore, "store_id", dict(
            store_id=s.store_id, city=city, country=country, last_update=s.last_update), stats)

def _sync_dim_customer(src, dst, mk, stats) -> None:
    rows = src.execute(
        select(Customer, City.city, Country.country)
        .join(Address, Customer.address_id == Address.address_id)
        .join(City, Address.city_id == City.city_id)
        .join(Country, City.country_id == Country.country_id)
        .where(or_(_changed(Customer.last_update, mk["customer"]),
                   _changed(Address.last_update, mk["address"]),
                   _changed(City.last_update, mk["city"]),
                   _changed(Country.last_update, mk["country"])))
    ).all()
    for c, city, country in rows:
        _upsert(dst, DimCustomer, "customer_id", dict(
            customer_id=c.customer_id, first_name=c.first_name, last_name=c.last_name,
            active=c.active, city=city, country=country, last_update=c.last_update), stats)

def _sync_bridges(src, dst, mk, film_keys, actor_keys, category_keys, stats) -> None:
    for r in src.scalars(select(FilmActor).where(_changed(FilmActor.last_update, mk["film_actor"]))):
        fk, ak = film_keys[r.film_id], actor_keys[r.actor_id]
        if dst.get(BridgeFilmActor, (fk, ak)) is None:
            dst.add(BridgeFilmActor(film_key=fk, actor_key=ak))
            stats["bridge_film_actor"]["inserted"] += 1
    for r in src.scalars(select(FilmCategory).where(_changed(FilmCategory.last_update, mk["film_category"]))):
        fk, ck = film_keys[r.film_id], category_keys[r.category_id]
        if dst.get(BridgeFilmCategory, (fk, ck)) is None:
            dst.add(BridgeFilmCategory(film_key=fk, category_key=ck))
            stats["bridge_film_category"]["inserted"] += 1

def _ensure_dim_date(dst: Session, dates: set[date], stats: Stats) -> None:
    keys = {date_key(d): d for d in dates}
    if not keys:
        return
    existing = set(dst.scalars(select(DimDate.date_key).where(DimDate.date_key.in_(keys))))
    for k, d in sorted(keys.items()):
        if k not in existing:
            dst.add(make_dim_date(d))
            stats["dim_date"]["inserted"] += 1
    dst.flush()

def _sync_facts(src, dst, mk, film_keys, store_keys, customer_keys, stats) -> None:
    rental_rows = src.execute(
        select(Rental, Inventory.film_id, Inventory.store_id)
        .join(Inventory, Rental.inventory_id == Inventory.inventory_id)
        .where(or_(_changed(Rental.last_update, mk["rental"]),
                   _changed(Inventory.last_update, mk["inventory"])))
    ).all()
    payment_rows = src.execute(
        select(Payment, Inventory.store_id)
        .outerjoin(Rental, Payment.rental_id == Rental.rental_id)
        .outerjoin(Inventory, Rental.inventory_id == Inventory.inventory_id)
        .where(or_(_changed(Payment.last_update, mk["payment"]),
                   Inventory.last_update >= mk["inventory"] if mk["inventory"] else true()))
    ).all()
    staff_store = {s.staff_id: s.store_id for s in src.scalars(select(Staff))}

    dates: set[date] = set()
    for r, _, _ in rental_rows:
        dates.add(r.rental_date.date())
        if r.return_date:
            dates.add(r.return_date.date())
    dates.update(p.payment_date.date() for p, _ in payment_rows)
    _ensure_dim_date(dst, dates, stats)

    for r, film_id, store_id in rental_rows:
        days = (r.return_date.date() - r.rental_date.date()).days if r.return_date else None
        _upsert(dst, FactRental, "rental_id", dict(
            rental_id=r.rental_id, date_key_rented=date_key(r.rental_date),
            date_key_returned=date_key(r.return_date), film_key=film_keys[film_id],
            store_key=store_keys[store_id], customer_key=customer_keys[r.customer_id],
            staff_id=r.staff_id, rental_duration_days=days), stats)

    for p, inv_store in payment_rows:
        store_id = inv_store if inv_store is not None else staff_store[p.staff_id]
        _upsert(dst, FactPayment, "payment_id", dict(
            payment_id=p.payment_id, date_key_paid=date_key(p.payment_date),
            customer_key=customer_keys[p.customer_id], store_key=store_keys[store_id],
            staff_id=p.staff_id, amount=p.amount), stats)

def incremental(src: Session, dst: Session) -> Stats:
    """Run one incremental sync in a single transaction, returning per-table counts."""
    stats: Stats = defaultdict(lambda: {"inserted": 0, "updated": 0})
    try:
        markers = _read_markers(dst)

        snapshot = {m.__tablename__: src.scalar(select(func.max(m.last_update)))
                    for m in SOURCE_MODELS}

        _sync_dim_film(src, dst, markers, stats)
        _sync_dim_actor(src, dst, markers, stats)
        _sync_dim_category(src, dst, markers, stats)
        _sync_dim_store(src, dst, markers, stats)
        _sync_dim_customer(src, dst, markers, stats)
        
        dst.flush()

        film_keys = _key_map(dst, DimFilm, "film_id", "film_key")
        actor_keys = _key_map(dst, DimActor, "actor_id", "actor_key")
        category_keys = _key_map(dst, DimCategory, "category_id", "category_key")
        store_keys = _key_map(dst, DimStore, "store_id", "store_key")
        customer_keys = _key_map(dst, DimCustomer, "customer_id", "customer_key")

        _sync_bridges(src, dst, markers, film_keys, actor_keys, category_keys, stats)
        _sync_facts(src, dst, markers, film_keys, store_keys, customer_keys, stats)

        for table, new in snapshot.items():
            old = markers[table]
            if new is not None and (old is None or new > old):
                dst.merge(SyncState(source_table=table, last_update=new))

        dst.commit()
    except Exception:
        dst.rollback()
        log.exception("Incremental sync failed. Analytics database rolled back.")
        raise

    for table, c in stats.items():
        log.info("%-22s inserted=%d updated=%d", table, c["inserted"], c["updated"])
    if not stats:
        log.info("no changes since last sync")
    return {t: dict(c) for t, c in stats.items()}
