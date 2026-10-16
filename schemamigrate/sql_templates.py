"""PostgreSQL templates used by Person A, B, and C.

SQL stays grouped by API and section here, avoiding a separate folder of
runtime .sql files while keeping each database operation readable.
"""

SQL_TEMPLATES = {'capture_api.sql': {'capture_function': 'CREATE OR REPLACE FUNCTION {meta}.capture_change()\n'
                                         'RETURNS trigger\n'
                                         'LANGUAGE plpgsql\n'
                                         'SECURITY DEFINER\n'
                                         'SET search_path = pg_catalog\n'
                                         'AS $function$\n'
                                         'BEGIN\n'
                                         "    IF TG_OP = 'INSERT' THEN\n"
                                         '        INSERT INTO {meta}.change_events (migration_id, '
                                         'direction, operation, new_row)\n'
                                         '        VALUES (TG_ARGV[0]::uuid, TG_ARGV[1], TG_OP, '
                                         'to_jsonb(NEW));\n'
                                         '        RETURN NEW;\n'
                                         "    ELSIF TG_OP = 'UPDATE' THEN\n"
                                         '        INSERT INTO {meta}.change_events (migration_id, '
                                         'direction, operation, old_row, new_row)\n'
                                         '        VALUES (TG_ARGV[0]::uuid, TG_ARGV[1], TG_OP, '
                                         'to_jsonb(OLD), to_jsonb(NEW));\n'
                                         '        RETURN NEW;\n'
                                         '    ELSE\n'
                                         '        INSERT INTO {meta}.change_events (migration_id, '
                                         'direction, operation, old_row)\n'
                                         '        VALUES (TG_ARGV[0]::uuid, TG_ARGV[1], TG_OP, '
                                         'to_jsonb(OLD));\n'
                                         '        RETURN OLD;\n'
                                         '    END IF;\n'
                                         'END;\n'
                                         '$function$'},
 'catchup_api.sql': {'delete_target_key': 'DELETE FROM {target} WHERE {key} = {typed_key}',
                     'find_current_row': 'SELECT {columns} FROM {source}\n'
                                         'WHERE {key} = (jsonb_populate_record(NULL::{source}, '
                                         '%s::jsonb)).{key}',
                     'mark_event_processed': 'UPDATE {meta}.change_events SET processed = true '
                                             'WHERE event_id = %s',
                     'pending_events': 'SELECT event_id, old_row, new_row\n'
                                       'FROM {meta}.change_events\n'
                                       'WHERE migration_id = %s AND direction = %s AND processed = '
                                       'false\n'
                                       'ORDER BY event_id LIMIT %s FOR UPDATE SKIP LOCKED',
                     'upsert_current_row': 'INSERT INTO {target} ({columns}) VALUES ({values})\n'
                                           'ON CONFLICT ({key}) {conflict_action}'},
 'copy_api.sql': {'copy_complete': 'UPDATE {meta}.migrations SET copy_complete = true, status = '
                                   "'COPY_READY'\n"
                                   'WHERE migration_id = %s',
                  'insert_batch': 'INSERT INTO {target} ({columns}) VALUES ({values})\n'
                                  'ON CONFLICT ({key}) {conflict_action}',
                  'save_copy_progress': 'UPDATE {meta}.migrations\n'
                                        'SET last_key = %s, copied_rows = copied_rows + %s, status '
                                        "= 'COPYING'\n"
                                        'WHERE migration_id = %s',
                  'select_after_key': 'SELECT {columns} FROM {source}\n'
                                      'WHERE {key} > (jsonb_populate_record(NULL::{source}, '
                                      '%s::jsonb)).{key}\n'
                                      'ORDER BY {key} LIMIT %s',
                  'select_first_batch': 'SELECT {columns} FROM {source} ORDER BY {key} LIMIT %s'},
 'cutover_api.sql': {'create_reverse_trigger': 'CREATE TRIGGER {trigger_name} AFTER INSERT OR '
                                               'UPDATE OR DELETE ON {table}\n'
                                               'FOR EACH ROW EXECUTE FUNCTION '
                                               "{meta}.capture_change(%s, 'REVERSE')",
                     'drop_trigger': 'DROP TRIGGER {trigger_name} ON {table}',
                     'lock_table': 'LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE',
                     'mark_rolled_back': "UPDATE {meta}.migrations SET status = 'ROLLED_BACK' "
                                         'WHERE migration_id = %s',
                     'mark_swapped': 'UPDATE {meta}.migrations SET old_table = %s, status = '
                                     "'SWAPPED', swapped_at = now()\n"
                                     'WHERE migration_id = %s',
                     'rename_table': 'ALTER TABLE {table} RENAME TO {new_name}'},
 'lifecycle_api.sql': {'abort_get': 'SELECT source_schema, source_table, shadow_table, status\n'
                                    'FROM {meta}.migrations WHERE migration_id = %s FOR UPDATE',
                       'drop_table': 'DROP TABLE {table}',
                       'drop_trigger_if_exists': 'DROP TRIGGER IF EXISTS {trigger_name} ON {table}',
                       'finalize_get': 'SELECT source_schema, source_table, shadow_table, '
                                       'old_table, status\n'
                                       'FROM {meta}.migrations WHERE migration_id = %s FOR UPDATE',
                       'mark_aborted': "UPDATE {meta}.migrations SET status = 'ABORTED' WHERE "
                                       'migration_id = %s',
                       'mark_finalized': 'UPDATE {meta}.migrations SET status = %s WHERE '
                                         'migration_id = %s'},
 'prepare_api.sql': {'add_requested_column': 'ALTER TABLE {shadow} ADD COLUMN {column_definition}',
                     'create_capture_trigger': 'CREATE TRIGGER {trigger_name} AFTER INSERT OR '
                                               'UPDATE OR DELETE ON {source}\n'
                                               'FOR EACH ROW EXECUTE FUNCTION '
                                               '{meta}.capture_change(%s, %s)',
                     'create_shadow': 'CREATE TABLE {shadow} (LIKE {source} INCLUDING ALL)',
                     'insert_migration': 'INSERT INTO {meta}.migrations (\n'
                                         '    migration_id, source_schema, source_table, '
                                         'shadow_table,\n'
                                         '    requested_column, source_columns, '
                                         'primary_key_columns,\n'
                                         '    estimated_rows, source_size_bytes, plan, status\n'
                                         ') VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '
                                         "'READY_FOR_COPY')",
                     'lock_source_for_prepare': 'LOCK TABLE {source} IN SHARE ROW EXCLUSIVE MODE',
                     'requested_column_exists': 'SELECT EXISTS (\n'
                                                '  SELECT 1 FROM information_schema.columns\n'
                                                '  WHERE table_schema = %s AND table_name = %s AND '
                                                'column_name = %s\n'
                                                ')'},
 'state_api.sql': {'create_events': 'CREATE TABLE IF NOT EXISTS {meta}.change_events (\n'
                                    '    event_id bigserial PRIMARY KEY,\n'
                                    '    migration_id uuid NOT NULL,\n'
                                    "    direction text NOT NULL DEFAULT 'FORWARD',\n"
                                    '    operation text NOT NULL,\n'
                                    '    old_row jsonb,\n'
                                    '    new_row jsonb,\n'
                                    '    processed boolean NOT NULL DEFAULT false,\n'
                                    '    captured_at timestamptz NOT NULL DEFAULT now()\n'
                                    ')',
                   'create_migrations': 'CREATE TABLE IF NOT EXISTS {meta}.migrations (\n'
                                        '    migration_id uuid PRIMARY KEY,\n'
                                        '    source_schema text NOT NULL,\n'
                                        '    source_table text NOT NULL,\n'
                                        '    shadow_table text NOT NULL,\n'
                                        '    old_table text,\n'
                                        '    requested_column text NOT NULL,\n'
                                        '    source_columns jsonb NOT NULL,\n'
                                        '    primary_key_columns jsonb NOT NULL,\n'
                                        '    estimated_rows bigint,\n'
                                        '    source_size_bytes bigint,\n'
                                        '    plan jsonb NOT NULL,\n'
                                        '    status text NOT NULL,\n'
                                        '    last_key jsonb,\n'
                                        '    copied_rows bigint NOT NULL DEFAULT 0,\n'
                                        '    copy_complete boolean NOT NULL DEFAULT false,\n'
                                        '    prepared_at timestamptz NOT NULL DEFAULT now(),\n'
                                        '    swapped_at timestamptz,\n'
                                        '    handoff jsonb\n'
                                        ')',
                   'create_pending_index': 'CREATE INDEX IF NOT EXISTS '
                                           'schemamigrate_change_events_pending\n'
                                           'ON {meta}.change_events (migration_id, direction, '
                                           'event_id)\n'
                                           'WHERE processed = false',
                   'create_schema': 'CREATE SCHEMA IF NOT EXISTS {meta}',
                   'get_migration': 'SELECT source_schema, source_table, shadow_table, old_table, '
                                    'source_columns,\n'
                                    '       primary_key_columns, last_key, copied_rows, '
                                    'copy_complete, status\n'
                                    'FROM {meta}.migrations WHERE migration_id = %s FOR UPDATE',
                   'get_migration_read': 'SELECT source_schema, source_table, shadow_table, '
                                         'old_table, source_columns,\n'
                                         '       primary_key_columns, last_key, copied_rows, '
                                         'copy_complete, status\n'
                                         'FROM {meta}.migrations WHERE migration_id = %s',
                   'handoff_details': 'SELECT migration_id, source_schema, source_table, '
                                      'shadow_table,\n'
                                      '       requested_column, source_columns, '
                                      'primary_key_columns, estimated_rows,\n'
                                      '       source_size_bytes, plan, status, last_key, '
                                      'copied_rows, copy_complete\n'
                                      'FROM {meta}.migrations WHERE migration_id = %s',
                   'migration_status': 'SELECT migration_id, source_schema, source_table, '
                                       'shadow_table, old_table,\n'
                                       '       status, copied_rows, copy_complete, prepared_at, '
                                       'swapped_at\n'
                                       'FROM {meta}.migrations WHERE migration_id = %s',
                   'save_handoff': 'UPDATE {meta}.migrations SET handoff = %s WHERE migration_id = '
                                   '%s'},
 'verify_api.sql': {'row_count': 'SELECT count(*) FROM {table}',
                    'rows_differ': 'SELECT EXISTS (\n'
                                   '    (SELECT {columns} FROM {left_table} EXCEPT ALL SELECT '
                                   '{columns} FROM {right_table})\n'
                                   '    UNION ALL\n'
                                   '    (SELECT {columns} FROM {right_table} EXCEPT ALL SELECT '
                                   '{columns} FROM {left_table})\n'
                                   ')'}}
