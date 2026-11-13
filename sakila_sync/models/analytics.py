from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import String, ForeignKey, Index, Numeric
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class AnalyticsBase(DeclarativeBase):
    pass

class DimDate(AnalyticsBase):
    __tablename__ = "dim_date"

    date_key: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date]
    year: Mapped[int]
    quarter: Mapped[int]
    month: Mapped[int]
    day_of_month: Mapped[int]
    day_of_week: Mapped[int]
    is_weekend: Mapped[int]

class DimFilm(AnalyticsBase):
    __tablename__ = "dim_film"

    film_key: Mapped[int] = mapped_column(primary_key=True)
    film_id: Mapped[int] = mapped_column(unique=True)
    title: Mapped[str] = mapped_column(String(128))
    rating: Mapped[str | None] = mapped_column(String(10))
    length: Mapped[int | None]
    language: Mapped[str] = mapped_column(String(20))
    release_year: Mapped[int | None]
    last_update: Mapped[datetime]

class DimActor(AnalyticsBase):
    __tablename__ = "dim_actor"

    actor_key: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(unique=True)
    first_name: Mapped[str] = mapped_column(String(45))
    last_name: Mapped[str] = mapped_column(String(45))
    last_update: Mapped[datetime]

class DimCategory(AnalyticsBase):
    __tablename__ = "dim_category"

    category_key: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(unique=True)
    name: Mapped[str] = mapped_column(String(25))
    last_update: Mapped[datetime]

class DimStore(AnalyticsBase):
    __tablename__ = "dim_store"

    store_key: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(unique=True)
    city: Mapped[str] = mapped_column(String(50))
    country: Mapped[str] = mapped_column(String(50))
    last_update: Mapped[datetime]

class DimCustomer(AnalyticsBase):
    __tablename__ = "dim_customer"

    customer_key: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(unique=True)
    first_name: Mapped[str] = mapped_column(String(45))
    last_name: Mapped[str] = mapped_column(String(45))
    active: Mapped[int]
    city: Mapped[str] = mapped_column(String(50))
    country: Mapped[str] = mapped_column(String(50))
    last_update: Mapped[datetime]

class BridgeFilmActor(AnalyticsBase):
    __tablename__ = "bridge_film_actor"
    __table_args__ = (Index("idx_bridge_film_actor_actor_key", "actor_key"),)

    film_key: Mapped[int] = mapped_column(ForeignKey("dim_film.film_key"), primary_key=True)
    actor_key: Mapped[int] = mapped_column(ForeignKey("dim_actor.actor_key"), primary_key=True)

class BridgeFilmCategory(AnalyticsBase):
    __tablename__ = "bridge_film_category"
    __table_args__ = (Index("idx_bridge_film_category_category_key", "category_key"),)

    film_key: Mapped[int] = mapped_column(ForeignKey("dim_film.film_key"), primary_key=True)
    category_key: Mapped[int] = mapped_column(ForeignKey("dim_category.category_key"), primary_key=True)

class FactRental(AnalyticsBase):
    __tablename__ = "fact_rental"
    __table_args__ = (
        Index("idx_fact_rental_date_key_rented", "date_key_rented"),
        Index("idx_fact_rental_date_key_returned", "date_key_returned"),
        Index("idx_fact_rental_film_key", "film_key"),
        Index("idx_fact_rental_store_key", "store_key"),
        Index("idx_fact_rental_customer_key", "customer_key"),
    )

    fact_rental_key: Mapped[int] = mapped_column(primary_key=True)
    rental_id: Mapped[int] = mapped_column(unique=True)
    date_key_rented: Mapped[int] = mapped_column(ForeignKey("dim_date.date_key"))
    date_key_returned: Mapped[int | None] = mapped_column(ForeignKey("dim_date.date_key"))
    film_key: Mapped[int] = mapped_column(ForeignKey("dim_film.film_key"))
    store_key: Mapped[int] = mapped_column(ForeignKey("dim_store.store_key"))
    customer_key: Mapped[int] = mapped_column(ForeignKey("dim_customer.customer_key"))
    staff_id: Mapped[int]
    rental_duration_days: Mapped[int | None]

class FactPayment(AnalyticsBase):
    __tablename__ = "fact_payment"
    __table_args__ = (
        Index("idx_fact_payment_date_key_paid", "date_key_paid"),
        Index("idx_fact_payment_customer_key", "customer_key"),
        Index("idx_fact_payment_store_key", "store_key"),
    )

    fact_payment_key: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(unique=True)
    date_key_paid: Mapped[int] = mapped_column(ForeignKey("dim_date.date_key"))
    customer_key: Mapped[int] = mapped_column(ForeignKey("dim_customer.customer_key"))
    store_key: Mapped[int] = mapped_column(ForeignKey("dim_store.store_key"))
    staff_id: Mapped[int]
    amount: Mapped[Decimal] = mapped_column(Numeric(5, 2))

class SyncState(AnalyticsBase):
    __tablename__ = "sync_state"

    source_table: Mapped[str] = mapped_column(String(50), primary_key=True)
    last_update: Mapped[datetime | None]