# -*- coding: utf-8 -*-
# Copyright 2017 bloopark systems (<http://bloopark.de>)
# Copyright 2018 Tecnativa - Pedro M. Baeza
# Copyright 2020 Opener B.V. (<https://opener.amsterdam>)
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

import logging
from openupgradelib import openupgrade

_logger = logging.getLogger('sale_stock.migrations.10.0.1.0')


def update_to_refund_so(env):
    cr = env.cr
    where_clause = "WHERE origin_returned_move_id IS NOT NULL"
    # If there's a migration from v8, use the field to say if return is
    # refundable or not
    column_name = 'openupgrade_legacy_9_0_invoice_state'
    if openupgrade.column_exists(cr, 'stock_move', column_name):
        where_clause += " AND %s != 'none'" % column_name
    query = "SELECT id FROM stock_move %s" % where_clause
    cr.execute(query)
    move_ids = [x[0] for x in cr.fetchall()]
    if not move_ids:
        return
    openupgrade.logged_query(
        cr, "UPDATE stock_move SET to_refund_so=True WHERE id IN %s",
        (tuple(move_ids), ),
    )

    # Select affected sale order lines: linked to the updated moves, but
    # excluding lines with a phantom bom product
    phantom_product_ids = [0]
    if openupgrade.is_module_installed(env.cr, 'sale_mrp'):
        env.cr.execute(
            """ SELECT product_id FROM mrp_bom
            WHERE active AND type = 'phantom' AND product_id IS NOT NULL
            UNION
            SELECT pp.id AS product_id FROM product_product pp
            JOIN mrp_bom mb ON
                mb.active AND type = 'phantom' AND mb.product_id IS NULL
                AND mb.product_tmpl_id = pp.product_tmpl_id """)
        phantom_product_ids = set([pid for pid, in env.cr.fetchall()])
    cr.execute(
        """ SELECT sol.id FROM sale_order_line sol
            JOIN procurement_order po ON po.sale_line_id = sol.id
            JOIN stock_move sm ON sm.procurement_id = po.id
        WHERE sm.id IN %s AND sol.product_id NOT IN %s """,
        (tuple(move_ids), tuple(phantom_product_ids)))
    sale_line_ids = [sale_line_id for sale_line_id, in env.cr.fetchall()]

    # Attempt SQL method for sales with stock moves in same UOM
    openupgrade.logged_query(
        env.cr,
        """ WITH qtys AS (
    SELECT sol.id, SUM(
        CASE WHEN sl.usage = 'customer' THEN 1
            ELSE -1 END * sm.product_uom_qty) AS qty_delivered,
        MAX(ABS(sol.product_uom - sm.product_uom)) AS other_uom
    FROM sale_order_line sol
        JOIN procurement_order po ON sol.id = po.sale_line_id
        JOIN stock_move sm ON sm.procurement_id = po.id
        JOIN stock_location sl ON sm.location_dest_id = sl.id
    WHERE sol.id IN %s AND po.sale_line_id = sol.id
        AND sm.state = 'done' AND sm.scrapped IS NOT TRUE
        AND ((sl.usage = 'customer' AND (
             sm.origin_returned_move_id IS NULL OR sm.to_refund_so))
             OR (sl.usage != 'customer' AND sm.to_refund_so))
    GROUP BY sol.id)
    UPDATE sale_order_line sol
    SET qty_delivered = qtys.qty_delivered
    FROM qtys
    WHERE sol.id = qtys.id
        AND other_uom = 0
        RETURNING sol.id """, (tuple(sale_line_ids or [0]),))
    done_line_ids = [line_id for line_id, in env.cr.fetchall()]

    # Update quantity to invoice for products invoiced on delivery
    openupgrade.logged_query(
        env.cr, """
    UPDATE sale_order_line sol
    SET qty_to_invoice = ROUND(
        qty_delivered - qty_invoiced,
        LOG(1 / pu.rounding)::INTEGER)
    FROM product_product pp
    JOIN product_template pt ON pt.id = pp.product_tmpl_id
    JOIN product_uom pu ON pu.id = pt.uom_id
    WHERE pp.id = sol.product_id
        AND pt.invoice_policy = 'delivery'
        AND sol.id IN %s""", (tuple(done_line_ids or [0]),))

    # Update invoice status based on possibly increased quantities to invoice
    openupgrade.logged_query(
        env.cr, """
    UPDATE sale_order_line sol
    SET invoice_status =
        CASE WHEN ROUND(
            sol.qty_to_invoice,
            LOG(1 / pu.rounding)::INTEGER) > 0 THEN 'to invoice'
        ELSE 'invoiced' END
    FROM product_product pp
    JOIN product_template pt ON pt.id = pp.product_tmpl_id
    JOIN product_uom pu ON pu.id = pt.uom_id
    WHERE pp.id = sol.product_id
        AND pt.invoice_policy = 'delivery'
        AND sol.state IN ('sale', 'done') AND sol.id IN %s
        RETURNING sol.order_id """,
        (tuple(done_line_ids or [0]),))
    order_ids = set([order_id for order_id, in env.cr.fetchall()])

    # Update order invoice status based on updated line invoice status
    openupgrade.logged_query(
        env.cr, """
    UPDATE sale_order so
    SET invoice_status =
        CASE WHEN EXISTS (
            SELECT 1 FROM sale_order_line sol
            WHERE order_id = so.id
                AND sol.invoice_status = 'to invoice')
            THEN 'to invoice'
            WHEN NOT EXISTS (
            SELECT 1 FROM sale_order_line
            WHERE order_id = so.id
                AND invoice_status != 'invoiced')
            THEN 'invoiced'
            WHEN NOT EXISTS (
            SELECT 1 FROM sale_order_line
            WHERE order_id = so.id
                AND invoice_status NOT IN ('invoiced', 'upselling'))
            THEN 'upselling'
            ELSE 'no'
        END
        WHERE id IN %s """, (tuple(order_ids or [0]),))

    remaining_ids = list(set(sale_line_ids) - set(done_line_ids))
    # Fallback on ORM for lines with stock moves in a different UOM
    _logger.debug('Recomputing delivery/invoice related fields for %s sale '
                  'order lines.', len(remaining_ids))
    sale_order_lines = env['sale.order.line'].browse(remaining_ids)
    for line in sale_order_lines:
        line.qty_delivered = line._get_delivered_qty()


@openupgrade.migrate(use_env=True)
def migrate(env, version):
    update_to_refund_so(env)
