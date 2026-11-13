from datetime import datetime, timedelta
from decimal import Decimal
from sqlalchemy import func, inspect, select

from sakila_sync.models.analytics import (
    AnalyticsBase, BridgeFilmActor, BridgeFilmCategory, DimActor, DimCategory,
    DimCustomer, DimDate, DimFilm, DimStore, FactPayment, FactRental, SyncState
)
from sakila_sync.models.sakila import (
    Actor, Address, Category, City, Customer, Film, FilmActor, FilmCategory,
    Payment, Rental, Store
)

def count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))

def now() -> datetime:
    return datetime.now().replace(microsecond=0)

def date_key_of(value: datetime) -> int:
    return int(value.strftime("%Y%m%d"))

def next_id(session, column) -> int:
    return session.scalar(select(func.max(column))) + 1

def test_init_creates_database_and_tables(run, analytics):
    code, out, _ = run("init")
    assert code == 0 and "init OK" in out

    dst = analytics()
    assert set(inspect(dst.get_bind()).get_table_names()) >= set(AnalyticsBase.metadata.tables)
    assert count(dst, SyncState) == 15
    assert count(dst, DimDate) > 0

def test_full_load_copies_all_data(run, analytics, sakila):
    run("init")
    code, out, _ = run("full-load")
    assert code == 0 and "full-load OK" in out

    dst = analytics()
    pairs = [(Film, DimFilm), (Actor, DimActor), (Category, DimCategory), (Store, DimStore),
             (Customer, DimCustomer), (FilmActor, BridgeFilmActor),
             (FilmCategory, BridgeFilmCategory), (Rental, FactRental), (Payment, FactPayment)]
    for source, target in pairs:
        assert count(sakila, source) == count(dst, target), target.__tablename__
    assert (sakila.scalar(select(func.sum(Payment.amount)))
            == dst.scalar(select(func.sum(FactPayment.amount))))

def test_incremental_new_data(run, analytics, sakila, loaded):
    ts = now()
    cust_id = next_id(sakila, Customer.customer_id)
    rental_id = next_id(sakila, Rental.rental_id)
    pay_id = next_id(sakila, Payment.payment_id)
    sakila.add_all([
        Customer(customer_id=cust_id, store_id=1, first_name="NEW", last_name="PERSON",
                 address_id=5, active=1, create_date=ts, last_update=ts),
        Rental(rental_id=rental_id, rental_date=datetime(2006, 3, 1, 10, 0), inventory_id=1,
               customer_id=cust_id, return_date=None, staff_id=1, last_update=ts),
        Payment(payment_id=pay_id, customer_id=cust_id, staff_id=1, rental_id=rental_id,
                amount=Decimal("2.99"), payment_date=datetime(2006, 3, 1, 10, 5), last_update=ts),
    ])
    sakila.commit()
    rented_key = date_key_of(sakila.get(Rental, rental_id).rental_date)
    paid_key = date_key_of(sakila.get(Payment, pay_id).payment_date)

    code, out, _ = run("incremental")
    assert code == 0 and "incremental OK" in out

    dst = analytics()
    cust = dst.scalar(select(DimCustomer).where(DimCustomer.customer_id == cust_id))
    assert cust.first_name == "NEW"
    rental = dst.scalar(select(FactRental).where(FactRental.rental_id == rental_id))
    assert rental.customer_key == cust.customer_key and rental.date_key_rented == rented_key
    assert rental.date_key_returned is None and rental.rental_duration_days is None
    pay = dst.scalar(select(FactPayment).where(FactPayment.payment_id == pay_id))
    assert pay.amount == Decimal("2.99") and pay.date_key_paid == paid_key

def test_incremental_updates(run, analytics, sakila, loaded):
    ts = now()
    rental = sakila.scalar(select(Rental).where(Rental.return_date.is_(None)).limit(1))
    rental_id, returned = rental.rental_id, rental.rental_date + timedelta(days=3)
    rental.return_date, rental.last_update = returned, ts

    new_address = sakila.get(Customer, 2).address_id
    customer = sakila.get(Customer, 1)
    customer.address_id, customer.last_update = new_address, ts
    sakila.commit()

    code, out, _ = run("incremental")
    assert code == 0, out

    dst = analytics()
    fact = dst.scalar(select(FactRental).where(FactRental.rental_id == rental_id))
    assert fact.rental_duration_days == 3
    assert fact.date_key_returned == date_key_of(returned)
    expected_city = sakila.get(City, sakila.get(Address, new_address).city_id).city
    assert dst.scalar(select(DimCustomer.city).where(DimCustomer.customer_id == 1)) == expected_city

def test_validate_detects_mismatch(run, analytics, loaded):
    code, out, _ = run("validate")
    assert code == 0 and "validate OK" in out

    dst = analytics()
    latest = dst.scalar(select(FactPayment).order_by(FactPayment.date_key_paid.desc()).limit(1))
    dst.delete(latest)
    dst.commit()

    code, _, err = run("validate")
    assert code != 0
    assert "validate FAILED" in err and "payment" in err
