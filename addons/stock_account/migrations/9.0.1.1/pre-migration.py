# -*- coding: utf-8 -*-
# Copyright 2016 Therp BV <http://therp.nl>
# Copyright 2017 Tecnativa - Pedro M. Baeza
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

from openupgradelib import openupgrade

field_renames = [
    # renamings with oldname attribute - They also need the rest of operations
    ('product.category', 'product_category',
     'property_stock_account_input_categ',
     'property_stock_account_input_categ_id'),
    ('product.category', 'product_category',
     'property_stock_account_output_categ',
     'property_stock_account_output_categ_id'),
]


def rename_property(cr, model, old_name, new_name):
    """Rename property old_name owned by model to new_name. This should happen
    in a pre-migration script.
    Don't use openupgradelib's version as it renames the field just like
    rename_fields does, and renaming twice is harmful.
    """
    openupgrade.logged_query(
        cr,
        """ \
        update ir_property ip
        set name = %(new_name)s
        from ir_model_fields imf
        where imf.model = %(model)s and imf.name in (%(old_name)s, %(new_name)s)
            and ip.fields_id = imf.id and ip.name = %(old_name)s
        """,
        {
            "old_name": old_name,
            "new_name": new_name,
            "model": model,
        })


@openupgrade.migrate(use_env=True)
def migrate(env, version):
    cr = env.cr
    rename_property(
        cr, 'product.template', 'cost_method', 'property_cost_method')
    rename_property(
        cr, 'product.template', 'valuation', 'property_valuation')
    rename_property(
        cr, 'product.category', 'property_stock_account_input_categ',
        'property_stock_account_input_categ_id')
    rename_property(
        cr, 'product.category', 'property_stock_account_output_categ',
        'property_stock_account_output_categ_id')
    openupgrade.rename_fields(env, field_renames)
