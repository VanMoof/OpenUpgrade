# -*- coding: utf-8 -*-
# © 2017 Therp BV <http://therp.nl>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
import csv
from openupgradelib import openupgrade
from odoo.addons.openupgrade_records.lib import apriori
from odoo.modules.module import get_module_resource

_column_renames = {
    'res_partner': [
        ('birthdate', None),
    ],
}


def ensure_country_state_id_on_existing_records(cr):
    """Suppose you have country states introduced manually.
    This method ensure you don't have problems later in the migration when
    loading the res.country.state.csv"""
    with open(get_module_resource('base', 'res', 'res.country.state.csv'),
              'rb') as country_states_file:
        states = csv.reader(country_states_file, delimiter=',', quotechar='"')
        for row, state in enumerate(states):
            if row == 0:
                continue
            data_name = state[0]
            country_code = state[1]
            name = state[2]
            state_code = state[3]
            # first: query to ensure the existing odoo countries have
            # the code of the csv file, because maybe some code has changed
            cr.execute(
                """
                UPDATE res_country_state rcs
                SET code = '%(state_code)s'
                FROM ir_model_data imd
                WHERE imd.model = 'res.country.state'
                    AND imd.res_id = rcs.id
                    AND imd.name = '%(data_name)s'
                """ % {
                    'state_code': state_code,
                    'data_name': data_name,
                }
            )
            # second: find if csv record exists in ir_model_data
            cr.execute(
                """
                SELECT imd.id
                FROM ir_model_data imd
                INNER JOIN res_country_state rcs ON (
                    imd.model = 'res.country.state' AND imd.res_id = rcs.id)
                LEFT JOIN res_country rc ON rcs.country_id = rc.id
                INNER JOIN ir_model_data imd2 ON (
                    rc.id = imd2.res_id AND imd2.model = 'res.country')
                WHERE imd2.name = '%(country_code)s'
                    AND rcs.code = '%(state_code)s'
                    AND imd.name = '%(data_name)s'
                """ % {
                    'country_code': country_code,
                    'state_code': state_code,
                    'data_name': data_name,
                }
            )
            found_id = cr.fetchone()
            if found_id:
                continue
            # third: as csv record not exists in ir_model_data, search for one
            # introduced manually that has same codes
            cr.execute(
                """
                SELECT imd.id
                FROM ir_model_data imd
                INNER JOIN res_country_state rcs ON (
                    imd.model = 'res.country.state' AND imd.res_id = rcs.id)
                LEFT JOIN res_country rc ON rcs.country_id = rc.id
                INNER JOIN ir_model_data imd2 ON (
                    rc.id = imd2.res_id AND imd2.model = 'res.country')
                WHERE imd2.name = '%(country_code)s'
                    AND rcs.code = '%(state_code)s'
                ORDER BY imd.id DESC
                LIMIT 1
                """ % {
                    'country_code': country_code,
                    'state_code': state_code,
                }
            )
            found_id = cr.fetchone()
            if found_id:
                # fourth: if found, ensure it has the same xmlid as the csv
                # record
                openupgrade.logged_query(
                    cr,
                    """
                    UPDATE ir_model_data
                    SET name = '%(data_name)s', module = 'base'
                    WHERE id = %(data_id)s AND model = 'res.country.state'
                    """ % {
                        'data_name': data_name,
                        'data_id': found_id[0],
                    }
                )
                cr.execute(
                    """
                    UPDATE res_country_state rcs
                    SET name = $$%(name)s$$
                    FROM ir_model_data imd
                    WHERE imd.id = %(data_id)s
                        AND imd.model = 'res.country.state'
                        AND imd.res_id = rcs.id
                    """ % {
                        'name': name,
                        'data_id': found_id[0],
                    }
                )
            else:
                # if res.country.state was created by hand via Odoo web
                # interface, it won't have any ir_model_data entry
                # -> we create one.
                cr.execute(
                    """
                    SELECT rcs.id
                    FROM res_country_state rcs
                    LEFT JOIN res_country rc ON rc.id=rcs.country_id
                    WHERE rcs.code='%(state_code)s'
                    AND rc.code = '%(country_code)s'
                    LIMIT 1
                    """ % {
                        'country_code': country_code.upper(),
                        'state_code': state_code.upper(),
                    }
                )
                found_id = cr.fetchone()
                if found_id:
                    openupgrade.add_xmlid(
                        cr, 'base', state[0], 'res.country.state', found_id)
        # fifth: search for duplicates, just in case, due to new constraint
        cr.execute(
            """
            SELECT imd.id, imd.name, rcs.code
            FROM ir_model_data imd
            INNER JOIN res_country_state rcs ON (
                imd.model = 'res.country.state' AND imd.res_id = rcs.id)
            ORDER BY imd.id DESC
            """
        )
        rows = []
        for row in cr.fetchall():
            if row in rows:
                # rename old duplicated entries that post-migration will merge
                openupgrade.logged_query(
                    cr,
                    """
                    UPDATE ir_model_data
                    SET name = $$%(data_name)s$$ || '_old_' || res_id
                    WHERE id = %(data_id)s AND model = 'res.country.state'
                    """ % {
                        'data_name': row[1],
                        'data_id': row[0],
                    }
                )
            else:
                rows.append(row)


def precreate_partner_fields(cr):
    """ Emulate stored computed methods in a single SQL query """
    cr.execute(
        """ALTER TABLE res_partner
        ADD COLUMN IF NOT EXISTS commercial_company_name VARCHAR,
        ADD COLUMN IF NOT EXISTS partner_share BOOLEAN
        """)
    openupgrade.logged_query(
        cr,
        """UPDATE res_partner rp
        SET commercial_company_name = crp.name
        FROM res_partner crp
        WHERE crp.id = rp.commercial_partner_id
            AND crp.is_company AND COALESCE(crp.name, '') != ''
        """)
    openupgrade.logged_query(
        cr,
        """UPDATE res_partner rp
        SET partner_share = NOT EXISTS (
            SELECT 1 FROM res_users
            WHERE partner_id = rp.id AND active)
        OR EXISTS (
            SELECT 1 FROM res_users
            WHERE partner_id = rp.id AND active AND share)
        """)


def align_partner_type_with_address_sync(cr):
    """use_parent_address is replaced by address type logic.

    Addresses are now synced with the parent if the partner type is 'contact'.
    Therefore, set addresses of type 'other' to 'contact' if they had the
    `use_parent_address` flag in 9.0, and vice versa.
    """
    openupgrade.logged_query(
        cr,
        """
        UPDATE res_partner
        SET type = 'contact'
        WHERE type = 'other'
            AND use_parent_address
            AND parent_id IS NOT NULL;
        """)
    openupgrade.logged_query(
        cr,
        """
        UPDATE res_partner
        SET type = 'other'
        WHERE type = 'contact'
            AND use_parent_address IS NOT TRUE
            AND parent_id IS NOT NULL;
        """)


@openupgrade.migrate(use_env=False)
def migrate(cr, version):
    openupgrade.update_module_names(
        cr, apriori.renamed_modules.iteritems()
    )
    openupgrade.rename_columns(cr, _column_renames)
    cr.execute(
        # we rely on the ORM to write this value
        'alter table ir_model_fields add column store boolean'
    )
    openupgrade.copy_columns(cr, {
        'ir_act_window': [
            ('target', None, None),
        ],
    })
    openupgrade.map_values(
        cr, openupgrade.get_legacy_name('target'), 'target',
        [
            ('inlineview', 'inline'),
        ],
        table='ir_act_window')
    cr.execute(
        "update ir_ui_view set type='kanban' where type='sales_team_dashboard'"
    )
    cr.execute('update res_currency set symbol=name where symbol is null')
    # create xmlids for installed languages
    cr.execute(
        '''insert into ir_model_data
        (module, name, model, res_id)
        select
        'base',
        'lang_' ||
        case
            when char_length(code) > 2 then
            case
                when upper(substring(code from 1 for 2)) =
                upper(substring(code from 4 for 2)) then
                    substring(code from 1 for 2)
                else
                    code
            end
            else
                code
        end,
        'res.lang', id
        from res_lang''')
    ensure_country_state_id_on_existing_records(cr)
    precreate_partner_fields(cr)
    align_partner_type_with_address_sync(cr)
    openupgrade.update_module_names(
        cr, apriori.merged_modules, merge_modules=True,
    )
