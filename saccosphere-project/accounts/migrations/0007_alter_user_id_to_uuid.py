from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0007_alter_user_id_to_uuid.py'),
    ]

    operations = [
        migrations.RunSQL(
            # Forward: convert bigint to uuid using gen_random_uuid() or a cast workaround
            sql="""
                ALTER TABLE accounts_user 
                ALTER COLUMN id TYPE uuid 
                USING (lpad(to_hex(id), 32, '0')::uuid);
            """,
            # Reverse: convert back to bigint
            reverse_sql="""
                ALTER TABLE accounts_user
                ALTER COLUMN id TYPE bigint
                USING (('x' || replace(id::text, '-', ''))::bit(64)::bigint);
            """
        ),
    ]