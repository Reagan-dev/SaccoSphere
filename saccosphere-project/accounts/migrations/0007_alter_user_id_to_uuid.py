from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0006_alter_otptoken_user'),  
    ]

    operations = [
        # Step 1: Add a new uuid column (nullable for now)
        migrations.RunSQL(
            sql="ALTER TABLE accounts_user ADD COLUMN new_uuid_id uuid;",
            reverse_sql="ALTER TABLE accounts_user DROP COLUMN new_uuid_id;",
        ),

        # Step 2: Populate it with generated UUIDs for every existing row
        migrations.RunSQL(
            sql="UPDATE accounts_user SET new_uuid_id = gen_random_uuid();",
            reverse_sql=migrations.RunSQL.noop,
        ),

        # Step 3: Make it NOT NULL
        migrations.RunSQL(
            sql="ALTER TABLE accounts_user ALTER COLUMN new_uuid_id SET NOT NULL;",
            reverse_sql="ALTER TABLE accounts_user ALTER COLUMN new_uuid_id DROP NOT NULL;",
        ),

        # Step 4: Drop the old bigint id column
        migrations.RunSQL(
            sql="ALTER TABLE accounts_user DROP COLUMN id;",
            reverse_sql=migrations.RunSQL.noop,
        ),

        # Step 5: Rename new column to id
        migrations.RunSQL(
            sql="ALTER TABLE accounts_user RENAME COLUMN new_uuid_id TO id;",
            reverse_sql=migrations.RunSQL.noop,
        ),

        # Step 6: Make it the primary key
        migrations.RunSQL(
            sql="ALTER TABLE accounts_user ADD PRIMARY KEY (id);",
            reverse_sql="ALTER TABLE accounts_user DROP CONSTRAINT accounts_user_pkey;",
        ),
    ]

    def apply(self, project_state, schema_editor, collect_sql=False):
        if schema_editor.connection.vendor != 'postgresql':
            return project_state
        return super().apply(project_state, schema_editor, collect_sql)

    def unapply(self, project_state, schema_editor, collect_sql=False):
        if schema_editor.connection.vendor != 'postgresql':
            return project_state
        return super().unapply(project_state, schema_editor, collect_sql)