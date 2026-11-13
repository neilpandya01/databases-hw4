import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

def mysql_session(test=False):
    db = os.environ["MYSQL_TEST_DATABASE" if test else "MYSQL_DATABASE"]
    url = (f"mysql+pymysql://{os.environ['MYSQL_USER']}:{os.environ['MYSQL_PASSWORD']}"
           f"@{os.environ['MYSQL_HOST']}:{os.environ.get('MYSQL_PORT', '3306')}/{db}")
    return sessionmaker(create_engine(url))()

def sqlite_session(test=False):
    path = os.environ["SQLITE_TEST_PATH" if test else "SQLITE_PATH"]
    return sessionmaker(create_engine(f"sqlite:///{path}"))()