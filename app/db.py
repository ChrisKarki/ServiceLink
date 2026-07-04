import os
from mysql.connector import pooling

_pool = pooling.MySQLConnectionPool(
    pool_name="servicelink_pool",
    pool_size=5,
    host=os.environ["DB_HOST"],
    port=int(os.environ.get("DB_PORT", 3306)),
    user=os.environ["DB_USER"],
    password=os.environ["DB_PASSWORD"],
    database=os.environ["DB_NAME"],
)


def get_conn():
    return _pool.get_connection()
