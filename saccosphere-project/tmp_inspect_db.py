import sqlite3
import os

path = os.path.join(os.getcwd(), 'db.sqlite3')
conn = sqlite3.connect(path)
cur = conn.cursor()

for tbl in ['payments_mpesatransaction', 'payments_callback', 'payments_transaction', 'payments_platformfee', 'django_migrations']:
    print('TABLE', tbl)
    try:
        cur.execute(f"PRAGMA table_info({tbl})")
        rows = cur.fetchall()
        if rows:
            for row in rows:
                print(row)
        else:
            print('NO ROWS / TABLE MISSING')
        if tbl == 'django_migrations':
            cur.execute("SELECT app,name FROM django_migrations WHERE app='payments'")
            for row in cur.fetchall():
                print('MIGRATION', row)
    except Exception as e:
        print('ERROR', tbl, e)
    print('---')

conn.close()
