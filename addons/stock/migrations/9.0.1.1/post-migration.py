# -*- coding: utf-8 -*-
# Copyright 2016 - Therp BV
# Copyright 2018 - Tecnativa - Pedro M. Baeza
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
from psycopg2.extensions import AsIs
from openerp import SUPERUSER_ID, api
from openupgradelib import openupgrade


def _migrate_tracking(cr):
    # set tracking to lot if tracking is switched on in the first place
    cr.execute(
        "update product_template set tracking='lot' where "
        "track_all or track_incoming or track_outgoing")
    # Some of them might be better off as serial tracking.
    # Maybe product_unique_serial was installed
    cr.execute(
        """ SELECT COUNT(*) FROM ir_model_data
        WHERE name = 'view_product_unique_serial_form' AND module = 'stock'
        """)
    if cr.fetchone()[0] and openupgrade.column_exists(
            cr, 'product_template', 'lot_unique_ok'):
        openupgrade.logged_query(
            cr,
            """\
            UPDATE product_template
            SET tracking = 'serial'
            WHERE lot_unique_ok AND tracking = 'lot'
            """)
    else:
        # Otherwise, we use lots of size 1 as indicator for that
        openupgrade.logged_query(
            cr,
            """\
            WITH lot_quantities AS (
                SELECT l.id, l.product_id, pp.product_tmpl_id,
                    SUM(qty) sum_qty
                FROM stock_production_lot l
                JOIN product_product pp ON pp.id = l.product_id
                JOIN stock_quant q ON q.lot_id = l.id
                GROUP BY l.id, pp.product_tmpl_id, l.product_id)
            UPDATE product_template pt
            SET tracking='serial'
            FROM lot_quantities
            WHERE pt.id = lot_quantities.product_tmpl_id
                AND pt.tracking = 'lot'
                AND NOT EXISTS (
                    SELECT id FROM lot_quantities
                    WHERE pt.id = lot_quantities.product_tmpl_id
                        AND lot_quantities.sum_qty <> 1); """)


def _migrate_pack_operation(env):
    """Create stock.pack.operation.lot records - non existing in v8 -,
    mark pickings that need to recreate pack operations, and update new field
    qty_done on stock.pack.operation for transferred pickings.
    """
    openupgrade.logged_query(
        env.cr,
        """ INSERT INTO stock_pack_operation_lot (
            id, lot_id, operation_id, qty_todo, qty,
            create_uid, write_uid, create_date, write_date)
        SELECT
            nextval('stock_pack_operation_lot_id_seq'),
            %(lot_id)s, o.id,
            CASE WHEN p.state != 'done' THEN o.product_qty ELSE 0 END,
            CASE WHEN p.state = 'done' THEN o.product_qty ELSE 0 END,
            o.create_uid, o.write_uid, o.create_date, o.write_date
        FROM stock_pack_operation o
        JOIN stock_picking p ON o.picking_id = p.id
        WHERE %(lot_id)s IS NOT NULL""",
        {'lot_id': AsIs(openupgrade.get_legacy_name('lot_id'))})
    openupgrade.logged_query(
        env.cr,
        "update stock_pack_operation "
        "set fresh_record = (%(processed)s = 'false')",
        {'processed': AsIs(openupgrade.get_legacy_name('processed'))})
    openupgrade.logged_query(
        env.cr, """
        UPDATE stock_pack_operation spo
        SET qty_done = product_qty
        FROM stock_picking p
        WHERE p.id = spo.picking_id
        AND p.state = 'done'"""
    )


def _migrate_stock_picking(cr):
    cr.execute(
        "update stock_picking p set "
        "location_id=coalesce(location_id, default_location_src_id), "
        "location_dest_id=coalesce(location_dest_id, "
        "default_location_dest_id) "
        "from stock_picking_type t "
        "where p.picking_type_id=t.id and "
        "(location_id is null or location_dest_id is null)")


def _set_lot_params(cr):
    cr.execute(
        "UPDATE stock_picking_type SET use_create_lots = "
        "code = 'incoming'")
    cr.execute(
        "UPDATE stock_picking_type SET use_existing_lots = "
        "code IN ('outgoing', 'internal')")


@openupgrade.migrate()
def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    _migrate_tracking(cr)
    _migrate_pack_operation(env)
    _migrate_stock_picking(cr)
    _set_lot_params(cr)
