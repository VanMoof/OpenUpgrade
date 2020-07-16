# -*- coding: utf-8 -*-
# Copyright 2018 ACSONE SA/NV (<http://acsone.eu>)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).
import logging
from openupgradelib import openupgrade
from openerp import api, SUPERUSER_ID


def phantom_bom_qty_delivered(env):
    """ Apply Odoo 12 logic to determine if sale lines with phantom bom
    products have been delivered: this is the case if there *are*
    stock moves from non-cancelled pickings, and all of these moves are
    in 'done' state. """
    logger = logging.getLogger('sale_mrp.migrations.9.0.1.1')
    env.cr.execute(
        """
        WITH lines AS (
            SELECT DISTINCT(sol.id) FROM sale_order_line sol
            JOIN product_product pp ON sol.product_id = pp.id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            JOIN mrp_bom mb ON mb.type = 'phantom'
                AND (mb.product_id = pp.id
                     OR (mb.product_tmpl_id = pt.id
                     AND mb.product_id is null)))
        SELECT lines.id, EXISTS(
            SELECT po.id FROM procurement_order po
            JOIN stock_move sm ON sm.procurement_id = po.id
            JOIN stock_picking sp ON sm.picking_id = sp.id
            WHERE po.sale_line_id = lines.id
                AND sp.state != 'cancel') AND NOT EXISTS(
            SELECT po.id FROM procurement_order po
            JOIN stock_move sm ON sm.procurement_id = po.id
            JOIN stock_picking sp ON sm.picking_id = sp.id
            WHERE po.sale_line_id = lines.id
                AND sp.state != 'cancel'
                AND sm.state != 'done')
        FROM lines;
        """)
    delivered = dict(env.cr.fetchall())
    logger.info(
        'Updating qty delivered on %s sale lines with phantom bom products',
        len(delivered))
    sale_lines = env['sale.order.line'].browse(delivered.keys())
    for sale_line in openupgrade.chunked(sale_lines):
        qty_delivered = (
            sale_line.product_uom_qty
            if delivered[sale_line.id] else 0.0)
        if sale_line.qty_delivered != qty_delivered:
            sale_line.qty_delivered = qty_delivered


@openupgrade.migrate()
def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    phantom_bom_qty_delivered(env)
