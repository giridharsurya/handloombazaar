import logging
import os
import time
from threading import Lock
from typing import Generator, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, declarative_base, sessionmaker
from config import config

Base = declarative_base()


logger = logging.getLogger(__name__)


class Database:
    def __init__(
        self,
        database_url: str,
        *,
        pool_size: int = 10,
        max_overflow: int = 20,
        pool_timeout: int = 30,
        pool_recycle: int = 1800,
        echo: bool = False,
        connect_retries: int = 5,
        retry_wait_seconds: float = 2.0,
    ) -> None:
        self.database_url = database_url
        self.pool_size = pool_size
        self.max_overflow = max_overflow
        self.pool_timeout = pool_timeout
        self.pool_recycle = pool_recycle
        self.echo = echo
        self.connect_retries = connect_retries
        self.retry_wait_seconds = retry_wait_seconds

        self._engine: Optional[Engine] = None
        self._session_factory: Optional[sessionmaker] = None
        self._lock = Lock()

    def _build_engine(self) -> Engine:
        return create_engine(
            self.database_url,
            echo=self.echo,
            pool_pre_ping=True,
            pool_size=self.pool_size,
            max_overflow=self.max_overflow,
            pool_timeout=self.pool_timeout,
            pool_recycle=self.pool_recycle,
            future=True,
        )

    def get_engine(self) -> Engine:
        self.ensure_connection()
        if self._engine is None:
            raise RuntimeError("Database engine is not initialized.")
        return self._engine
    
    def connect(self) -> None:
        with self._lock:
            if self._engine is not None and self.is_connection_alive():
                return

            self._dispose_without_lock()

            self._engine = self._build_engine()
            self._session_factory = sessionmaker(
                autocommit=False,
                autoflush=False,
                bind=self._engine,
                future=True,
            )

            # Validate an initial connection so startup fails fast if DB is unavailable.
            self._verify_connection_with_retry_locked()

    def disconnect(self) -> None:
        with self._lock:
            self._dispose_without_lock()

    def _dispose_without_lock(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
        self._engine = None
        self._session_factory = None

    def is_connection_alive(self) -> bool:
        if self._engine is None:
            return False

        try:
            with self._engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return True
        except SQLAlchemyError:
            return False

    def ensure_connection(self) -> None:
        if self.is_connection_alive():
            return

        with self._lock:
            if self._engine is not None and self.is_connection_alive():
                return

            self._dispose_without_lock()
            self._engine = self._build_engine()
            self._session_factory = sessionmaker(
                autocommit=False,
                autoflush=False,
                bind=self._engine,
                future=True,
            )
            self._verify_connection_with_retry_locked()

    def _verify_connection_with_retry_locked(self) -> None:
        last_error: Optional[BaseException] = None

        for attempt in range(1, self.connect_retries + 1):
            try:
                if self._engine is None:
                    raise RuntimeError("Database engine is not initialized.")

                with self._engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                return
            except (OperationalError, SQLAlchemyError) as exc:
                last_error = exc
                logger.warning(
                    "Database connection check failed (attempt %s/%s): %s",
                    attempt,
                    self.connect_retries,
                    exc,
                )

                if attempt < self.connect_retries:
                    time.sleep(self.retry_wait_seconds)

        raise RuntimeError(
            f"Database connection failed after {self.connect_retries} retries"
        ) from last_error

    def get_session(self) -> Session:
        self.ensure_connection()
        if self._session_factory is None:
            raise RuntimeError("Session factory is not initialized.")
        return self._session_factory()

    def session_scope(self) -> Generator[Session, None, None]:
        session = self.get_session()
        try:
            yield session
        finally:
            session.close()

db = Database(config.DATABASE_URL)


def get_session() -> Generator[Session, None, None]:
    session = db.get_session()
    try:
        yield session
    finally:
        session.close()

def get_engine() -> Engine:
    return db.get_engine()