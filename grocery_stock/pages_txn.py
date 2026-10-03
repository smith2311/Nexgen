"""
Grocery Stock — transaction page definitions (Phase 2)
─────────────────────────────────────────────────────────────────────────────
Same idea as pages.py: describe each screen as data and let one template plus
one script render it.

Three screen kinds:

    DOCUMENT_SPECS  draft-then-post documents (GRN, Issue) — a list and a form
    REPORT_SPECS    read-only tables (Current Stock, Stock Ledger)

Field/column ``type`` values match the renderers in the shared scripts:
    text · textarea · date · number · decimal · fk · select   (form)
    text · muted · num · money · qty · date · status · signed (table)
"""

# ═══ DOCUMENTS ══════════════════════════════════════════════════════════════

DOCUMENT_SPECS = {
    'receipts': {
        'title': 'Goods Receipt',
        'plural': 'Goods Receipts',
        'subtitle': 'Record stock arriving from a supplier — batch, expiry and landed cost',
        'icon': 'fas fa-truck-ramp-box',
        'tone': 'gs-tone-green',
        'endpoint': 'goods-receipts',
        'number_field': 'grn_number',
        'date_field': 'receipt_date',
        'write_roles_label': 'Grocery Manager',

        'list_columns': [
            {'key': 'grn_number', 'label': 'GRN No.', 'width': '150px'},
            {'key': 'receipt_date', 'label': 'Date', 'type': 'date', 'width': '110px'},
            {'key': 'supplier_name', 'label': 'Supplier'},
            {'key': 'invoice_number', 'label': 'Invoice', 'type': 'muted', 'width': '130px'},
            {'key': 'warehouse_name', 'label': 'Warehouse', 'type': 'muted', 'width': '150px'},
            {'key': 'line_count', 'label': 'Lines', 'type': 'num', 'width': '80px'},
            {'key': 'total_amount', 'label': 'Invoice Total', 'type': 'money', 'width': '130px'},
            {'key': 'inventory_value', 'label': 'To Stock', 'type': 'money', 'width': '120px'},
            {'key': 'status', 'label': 'Status', 'type': 'status', 'width': '110px'},
        ],

        'header_fields': [
            {'name': 'supplier', 'label': 'Supplier', 'type': 'fk', 'required': True,
             'source': 'suppliers', 'source_label': 'name'},
            {'name': 'warehouse', 'label': 'Warehouse', 'type': 'fk', 'required': True,
             'source': 'warehouses', 'source_label': 'name'},
            {'name': 'receipt_date', 'label': 'Receipt Date', 'type': 'date',
             'required': True, 'default': 'today'},
            {'name': 'invoice_number', 'label': 'Invoice Number', 'type': 'text',
             'maxlength': 60},
            {'name': 'invoice_date', 'label': 'Invoice Date', 'type': 'date'},

            {'name': 'freight_amount', 'label': 'Freight', 'type': 'decimal',
             'step': '0.01', 'default': 0, 'section': 'Charges to apportion',
             'help': 'Spread across lines in proportion to their value at posting.'},
            {'name': 'loading_amount', 'label': 'Loading', 'type': 'decimal',
             'step': '0.01', 'default': 0},
            {'name': 'insurance_amount', 'label': 'Insurance', 'type': 'decimal',
             'step': '0.01', 'default': 0},
            {'name': 'other_charges', 'label': 'Other Charges', 'type': 'decimal',
             'step': '0.01', 'default': 0},

            {'name': 'remarks', 'label': 'Remarks', 'type': 'textarea', 'span': 2},
        ],

        'line_columns': [
            {'name': 'item', 'label': 'Item', 'type': 'fk', 'required': True,
             'source': 'items', 'source_label': 'name', 'width': '210px'},
            {'name': 'batch_number', 'label': 'Batch', 'type': 'text', 'width': '110px'},
            {'name': 'expiry_date', 'label': 'Expiry', 'type': 'date', 'width': '140px'},
            {'name': 'received_quantity', 'label': 'Received', 'type': 'decimal',
             'step': '0.001', 'required': True, 'width': '105px'},
            {'name': 'accepted_quantity', 'label': 'Accepted', 'type': 'decimal',
             'step': '0.001', 'required': True, 'width': '105px'},
            {'name': 'rejected_quantity', 'label': 'Rejected', 'type': 'decimal',
             'step': '0.001', 'width': '105px'},
            {'name': 'unit_price', 'label': 'Rate', 'type': 'decimal',
             'step': '0.0001', 'required': True, 'width': '110px'},
            {'name': 'discount_amount', 'label': 'Discount', 'type': 'decimal',
             'step': '0.01', 'width': '100px'},
            {'name': 'tax_percent', 'label': 'GST %', 'type': 'decimal',
             'step': '0.01', 'width': '90px'},
        ],
        # Read-only columns appended after posting.
        'line_result_columns': [
            {'key': 'line_total', 'label': 'Line Total', 'type': 'money'},
            {'key': 'apportioned_charges', 'label': 'Charges', 'type': 'money'},
            {'key': 'landed_unit_cost', 'label': 'Landed Cost', 'type': 'money'},
        ],
    },

    'issues': {
        'title': 'Stock Issue',
        'plural': 'Stock Issues',
        'subtitle': 'Issue stock to a consuming department — costed at posting',
        'icon': 'fas fa-cart-arrow-down',
        'tone': 'gs-tone-amber',
        'endpoint': 'stock-issues',
        'number_field': 'issue_number',
        'date_field': 'issue_date',
        'write_roles_label': 'Grocery Manager',

        'list_columns': [
            {'key': 'issue_number', 'label': 'Issue No.', 'width': '150px'},
            {'key': 'issue_date', 'label': 'Date', 'type': 'date', 'width': '110px'},
            {'key': 'department_name', 'label': 'Department'},
            {'key': 'issued_to', 'label': 'Issued To', 'type': 'muted'},
            {'key': 'warehouse_name', 'label': 'Warehouse', 'type': 'muted', 'width': '150px'},
            {'key': 'line_count', 'label': 'Lines', 'type': 'num', 'width': '80px'},
            {'key': 'total_value', 'label': 'Value', 'type': 'money', 'width': '130px'},
            {'key': 'status', 'label': 'Status', 'type': 'status', 'width': '110px'},
        ],

        'header_fields': [
            {'name': 'department', 'label': 'Department', 'type': 'fk', 'required': True,
             'source': 'departments', 'source_label': 'name'},
            {'name': 'warehouse', 'label': 'Warehouse', 'type': 'fk', 'required': True,
             'source': 'warehouses', 'source_label': 'name'},
            {'name': 'issue_date', 'label': 'Issue Date', 'type': 'date',
             'required': True, 'default': 'today'},
            {'name': 'issued_to', 'label': 'Issued To', 'type': 'text', 'maxlength': 150},
            {'name': 'purpose', 'label': 'Purpose', 'type': 'text', 'maxlength': 255,
             'span': 2},
            {'name': 'remarks', 'label': 'Remarks', 'type': 'textarea', 'span': 2},
        ],

        'line_columns': [
            {'name': 'item', 'label': 'Item', 'type': 'fk', 'required': True,
             'source': 'items', 'source_label': 'name', 'width': '280px'},
            {'name': 'quantity', 'label': 'Quantity', 'type': 'decimal',
             'step': '0.001', 'required': True, 'width': '130px'},
            {'name': 'remarks', 'label': 'Remarks', 'type': 'text', 'width': '220px'},
        ],
        'line_result_columns': [
            {'key': 'unit_cost', 'label': 'Unit Cost', 'type': 'money'},
            {'key': 'line_value', 'label': 'Value', 'type': 'money'},
        ],
        # Shows live availability beside each line — an issue that exceeds
        # stock is rejected at posting, so warning during entry saves a round
        # trip and a confusing error.
        'show_availability': True,
    },

    'adjustments': {
        'title': 'Stock Adjustment',
        'plural': 'Stock Adjustments',
        'subtitle': 'Reconcile book stock to a physical count, or write off damage and expiry',
        'icon': 'fas fa-scale-balanced',
        'tone': 'gs-tone-purple',
        'endpoint': 'adjustments',
        'number_field': 'adjustment_number',
        'date_field': 'adjustment_date',
        'write_roles_label': 'Grocery Manager',

        'list_columns': [
            {'key': 'adjustment_number', 'label': 'Adj No.', 'width': '150px'},
            {'key': 'adjustment_date', 'label': 'Date', 'type': 'date', 'width': '110px'},
            {'key': 'reason_display', 'label': 'Reason', 'width': '150px'},
            {'key': 'warehouse_name', 'label': 'Warehouse'},
            {'key': 'reference', 'label': 'Reference', 'type': 'muted', 'width': '160px'},
            {'key': 'line_count', 'label': 'Lines', 'type': 'num', 'width': '80px'},
            {'key': 'total_value', 'label': 'Net Value', 'type': 'signed_money',
             'width': '130px'},
            {'key': 'status', 'label': 'Status', 'type': 'status', 'width': '110px'},
        ],

        'header_fields': [
            {'name': 'warehouse', 'label': 'Warehouse', 'type': 'fk', 'required': True,
             'source': 'warehouses', 'source_label': 'name'},
            {'name': 'reason', 'label': 'Reason', 'type': 'select', 'required': True,
             'default': 'PHYSICAL_COUNT', 'choices': [
                 {'value': 'PHYSICAL_COUNT', 'label': 'Physical Count'},
                 {'value': 'DAMAGE', 'label': 'Damage'},
                 {'value': 'THEFT', 'label': 'Theft'},
                 {'value': 'EXPIRY', 'label': 'Expiry'},
                 {'value': 'BREAKAGE', 'label': 'Breakage'},
             ],
             'help': 'One document is one event, so the reason applies to every line.'},
            {'name': 'adjustment_date', 'label': 'Date', 'type': 'date',
             'required': True, 'default': 'today'},
            {'name': 'reference', 'label': 'Reference', 'type': 'text', 'maxlength': 100,
             'placeholder': 'Stocktake sheet / incident report'},
            {'name': 'remarks', 'label': 'Remarks', 'type': 'textarea', 'span': 2},
        ],

        'line_columns': [
            {'name': 'item', 'label': 'Item', 'type': 'fk', 'required': True,
             'source': 'items', 'source_label': 'name', 'width': '260px'},
            {'name': 'counted_quantity', 'label': 'Counted Qty', 'type': 'decimal',
             'step': '0.001', 'required': True, 'width': '130px'},
            {'name': 'remarks', 'label': 'Remarks', 'type': 'text', 'width': '220px'},
        ],
        'line_result_columns': [
            {'key': 'system_quantity', 'label': 'System', 'type': 'qty'},
            {'key': 'difference', 'label': 'Difference', 'type': 'signed'},
            {'key': 'unit_cost', 'label': 'Unit Cost', 'type': 'money'},
            {'key': 'line_value', 'label': 'Value', 'type': 'signed_money'},
        ],
        # Shows the current book quantity beside each line so the person
        # entering a count can see the variance before posting.
        'show_availability': True,
        'availability_label': 'System Qty / Variance',
        'quantity_field': 'counted_quantity',
    },
}


# ═══ REPORTS ════════════════════════════════════════════════════════════════

REPORT_SPECS = {
    'stock': {
        'title': 'Current Stock',
        'subtitle': 'Live quantity and value by item and warehouse',
        'icon': 'fas fa-boxes-stacked',
        'tone': 'gs-tone-blue',
        'endpoint': 'stock',
        'search_placeholder': 'Search item or warehouse…',
        'columns': [
            {'key': 'item_name', 'label': 'Item'},
            {'key': 'category_name', 'label': 'Category', 'type': 'muted', 'width': '130px'},
            {'key': 'warehouse_code', 'label': 'Warehouse', 'width': '120px'},
            {'key': 'location_label', 'label': 'Bin', 'type': 'muted', 'width': '120px'},
            {'key': 'quantity_available', 'label': 'Available', 'type': 'qty', 'width': '110px'},
            {'key': 'unit_code', 'label': 'Unit', 'type': 'muted', 'width': '80px'},
            {'key': 'average_cost', 'label': 'Avg Cost', 'type': 'money', 'width': '110px'},
            {'key': 'stock_value', 'label': 'Value', 'type': 'money', 'width': '120px'},
            {'key': 'stock_status', 'label': 'Status', 'type': 'stock_status', 'width': '120px'},
        ],
        'filters': [
            {'name': 'warehouse', 'label': 'Warehouse', 'type': 'fk',
             'source': 'warehouses', 'source_label': 'name'},
            {'name': 'category', 'label': 'Category', 'type': 'fk',
             'source': 'categories', 'source_label': 'name'},
            {'name': 'stock_status', 'label': 'Status', 'type': 'select', 'choices': [
                {'value': 'LOW', 'label': 'Low stock'},
                {'value': 'OUT_OF_STOCK', 'label': 'Out of stock'},
                {'value': 'OK', 'label': 'Healthy'},
            ]},
        ],
        'toggles': [
            {'name': 'nonzero', 'label': 'Hide zero-stock rows'},
        ],
    },

    'ledger': {
        'title': 'Stock Ledger',
        'subtitle': 'Every movement, in and out — the audit trail behind current stock',
        'icon': 'fas fa-scroll',
        'tone': 'gs-tone-navy',
        'endpoint': 'ledger',
        'search_placeholder': 'Search item, reference or batch…',
        'columns': [
            {'key': 'moved_on', 'label': 'Date', 'type': 'date', 'width': '110px'},
            {'key': 'movement_display', 'label': 'Movement', 'type': 'movement',
             'width': '140px'},
            {'key': 'item_name', 'label': 'Item'},
            {'key': 'warehouse_code', 'label': 'WH', 'type': 'muted', 'width': '90px'},
            {'key': 'batch_number', 'label': 'Batch', 'type': 'muted', 'width': '110px'},
            {'key': 'department_name', 'label': 'Department', 'type': 'muted',
             'width': '120px'},
            {'key': 'quantity', 'label': 'Qty', 'type': 'signed', 'width': '110px'},
            {'key': 'unit_cost', 'label': 'Unit Cost', 'type': 'money', 'width': '110px'},
            {'key': 'value', 'label': 'Value', 'type': 'signed_money', 'width': '120px'},
            {'key': 'balance_quantity', 'label': 'Balance', 'type': 'qty', 'width': '110px'},
            {'key': 'reference_number', 'label': 'Reference', 'type': 'muted',
             'width': '150px'},
        ],
        'filters': [
            {'name': 'warehouse', 'label': 'Warehouse', 'type': 'fk',
             'source': 'warehouses', 'source_label': 'name'},
            {'name': 'department', 'label': 'Department', 'type': 'fk',
             'source': 'departments', 'source_label': 'name'},
            {'name': 'movement_type', 'label': 'Movement', 'type': 'select', 'choices': [
                {'value': 'RECEIPT', 'label': 'Goods Receipt'},
                {'value': 'ISSUE', 'label': 'Department Issue'},
                {'value': 'ADJUSTMENT', 'label': 'Adjustment'},
            ]},
        ],
        'date_range': True,
    },

    # ── Section 15: Inventory Reports ───────────────────────────────────────
    'valuation': {
        'title': 'Inventory Valuation',
        'subtitle': 'Quantity, average cost and value per item — the balance-sheet view',
        'icon': 'fas fa-indian-rupee-sign',
        'tone': 'gs-tone-teal',
        'endpoint': 'valuation',
        'group': 'Inventory',
        'search_placeholder': 'Search item…',
        'columns': [
            {'key': 'item_name', 'label': 'Item'},
            {'key': 'category_name', 'label': 'Category', 'type': 'muted', 'width': '140px'},
            {'key': 'quantity_on_hand', 'label': 'Qty on Hand', 'type': 'qty',
             'width': '120px'},
            {'key': 'unit_code', 'label': 'Unit', 'type': 'muted', 'width': '80px'},
            {'key': 'average_cost', 'label': 'Avg Cost', 'type': 'money', 'width': '110px'},
            {'key': 'total_value', 'label': 'Total Value', 'type': 'money', 'width': '130px'},
            {'key': 'valuation_method', 'label': 'Method', 'type': 'muted', 'width': '90px'},
            {'key': 'last_purchase_cost', 'label': 'Last Purchase', 'type': 'money',
             'width': '130px'},
        ],
        'filters': [
            {'name': 'category', 'label': 'Category', 'type': 'fk',
             'source': 'categories', 'source_label': 'name'},
        ],
        'toggles': [{'name': 'nonzero', 'label': 'Only items in stock'}],
    },

    'variance': {
        'title': 'Cost Variance',
        'subtitle': 'Actual average cost against the standard cost benchmark',
        'icon': 'fas fa-scale-unbalanced',
        'tone': 'gs-tone-amber',
        'endpoint': 'valuation',
        'group': 'Cost',
        'search_placeholder': 'Search item…',
        'columns': [
            {'key': 'item_name', 'label': 'Item'},
            {'key': 'category_name', 'label': 'Category', 'type': 'muted', 'width': '140px'},
            {'key': 'standard_cost', 'label': 'Standard', 'type': 'money', 'width': '120px'},
            {'key': 'average_cost', 'label': 'Actual Avg', 'type': 'money', 'width': '120px'},
            {'key': 'cost_variance', 'label': 'Variance', 'type': 'signed_money',
             'width': '120px'},
            {'key': 'quantity_on_hand', 'label': 'Qty', 'type': 'qty', 'width': '100px'},
            {'key': 'total_value', 'label': 'Value', 'type': 'money', 'width': '120px'},
        ],
        'filters': [
            {'name': 'category', 'label': 'Category', 'type': 'fk',
             'source': 'categories', 'source_label': 'name'},
        ],
        'toggles': [{'name': 'nonzero', 'label': 'Only items in stock'}],
    },

    'batches': {
        'title': 'Batch-wise Stock',
        'subtitle': 'Every open cost layer with its remaining quantity and cost',
        'icon': 'fas fa-layer-group',
        'tone': 'gs-tone-purple',
        'endpoint': 'batches',
        'group': 'Inventory',
        'search_placeholder': 'Search batch, item or GRN…',
        'columns': [
            {'key': 'item_name', 'label': 'Item'},
            {'key': 'batch_number', 'label': 'Batch', 'width': '120px'},
            {'key': 'warehouse_code', 'label': 'WH', 'type': 'muted', 'width': '90px'},
            {'key': 'received_on', 'label': 'Received', 'type': 'date', 'width': '110px'},
            {'key': 'expiry_date', 'label': 'Expiry', 'type': 'date', 'width': '110px'},
            {'key': 'remaining_quantity', 'label': 'Remaining', 'type': 'qty',
             'width': '110px'},
            {'key': 'unit_cost', 'label': 'Unit Cost', 'type': 'money', 'width': '110px'},
            {'key': 'reference_number', 'label': 'GRN', 'type': 'muted', 'width': '140px'},
        ],
        'filters': [
            {'name': 'warehouse', 'label': 'Warehouse', 'type': 'fk',
             'source': 'warehouses', 'source_label': 'name'},
        ],
        'toggles': [{'name': 'include_exhausted', 'label': 'Include used-up layers'}],
    },

    # ── Section 13 / 15: Expiry Reports ─────────────────────────────────────
    'expiry': {
        'title': 'Expiry Report',
        'subtitle': 'Batches expiring soon or already expired — act before stock is written off',
        'icon': 'fas fa-hourglass-half',
        'tone': 'gs-tone-red',
        'endpoint': 'batches',
        'group': 'Expiry',
        'search_placeholder': 'Search batch or item…',
        'columns': [
            {'key': 'expiry_status', 'label': 'Alert', 'type': 'expiry_status',
             'width': '120px'},
            {'key': 'days_to_expiry', 'label': 'Days Left', 'type': 'num', 'width': '100px'},
            {'key': 'expiry_date', 'label': 'Expiry', 'type': 'date', 'width': '110px'},
            {'key': 'item_name', 'label': 'Item'},
            {'key': 'batch_number', 'label': 'Batch', 'type': 'muted', 'width': '110px'},
            {'key': 'warehouse_code', 'label': 'WH', 'type': 'muted', 'width': '90px'},
            {'key': 'remaining_quantity', 'label': 'Remaining', 'type': 'qty',
             'width': '110px'},
            {'key': 'unit_cost', 'label': 'Unit Cost', 'type': 'money', 'width': '110px'},
        ],
        'filters': [
            {'name': 'warehouse', 'label': 'Warehouse', 'type': 'fk',
             'source': 'warehouses', 'source_label': 'name'},
            {'name': 'expiry_status', 'label': 'Alert band', 'type': 'select', 'choices': [
                {'value': 'EXPIRED', 'label': 'Expired'},
                {'value': 'CRITICAL', 'label': 'Within 30 days'},
                {'value': 'WARNING', 'label': 'Within 60 days'},
                {'value': 'NOTICE', 'label': 'Within 90 days'},
            ]},
        ],
        # Only dated, unconsumed layers matter here.
        'fixed_params': {'has_expiry': 'true'},
    },

    # ── Section 9 / 15: Consumption ─────────────────────────────────────────
    'consumption': {
        'title': 'Department Consumption',
        'subtitle': 'What each department consumed, and what it cost',
        'icon': 'fas fa-utensils',
        'tone': 'gs-tone-green',
        'endpoint': 'consumption',
        'group': 'Cost',
        'search_placeholder': 'Search department or item…',
        'sortable': False,
        'columns': [
            {'key': 'department_name', 'label': 'Department', 'width': '170px'},
            {'key': 'item_name', 'label': 'Item'},
            {'key': 'unit_code', 'label': 'Unit', 'type': 'muted', 'width': '80px'},
            {'key': 'quantity_consumed', 'label': 'Quantity', 'type': 'qty',
             'width': '120px'},
            {'key': 'cost_consumed', 'label': 'Cost', 'type': 'money', 'width': '130px'},
            {'key': 'movements', 'label': 'Movements', 'type': 'num', 'width': '110px'},
        ],
        'filters': [
            {'name': 'department', 'label': 'Department', 'type': 'fk',
             'source': 'departments', 'source_label': 'name'},
            {'name': 'category', 'label': 'Category', 'type': 'fk',
             'source': 'categories', 'source_label': 'name'},
        ],
        'date_range': True,
    },

    # ── Section 15: Purchase Reports ────────────────────────────────────────
    'purchases': {
        'title': 'Purchase Register',
        'subtitle': 'Every posted goods receipt with invoice and inventory value',
        'icon': 'fas fa-file-invoice-dollar',
        'tone': 'gs-tone-teal',
        'endpoint': 'goods-receipts',
        'group': 'Purchase',
        'search_placeholder': 'Search GRN, invoice or supplier…',
        'columns': [
            {'key': 'grn_number', 'label': 'GRN No.', 'width': '150px'},
            {'key': 'receipt_date', 'label': 'Date', 'type': 'date', 'width': '110px'},
            {'key': 'supplier_name', 'label': 'Supplier'},
            {'key': 'invoice_number', 'label': 'Invoice', 'type': 'muted', 'width': '140px'},
            {'key': 'warehouse_name', 'label': 'Warehouse', 'type': 'muted', 'width': '150px'},
            {'key': 'total_amount', 'label': 'Invoice Total', 'type': 'money',
             'width': '130px'},
            {'key': 'inventory_value', 'label': 'To Stock', 'type': 'money', 'width': '130px'},
            {'key': 'status', 'label': 'Status', 'type': 'status', 'width': '110px'},
        ],
        'filters': [
            {'name': 'supplier', 'label': 'Supplier', 'type': 'fk',
             'source': 'suppliers', 'source_label': 'name'},
            {'name': 'warehouse', 'label': 'Warehouse', 'type': 'fk',
             'source': 'warehouses', 'source_label': 'name'},
        ],
        'date_range': True,
        'fixed_params': {'status': 'POSTED'},
    },
}


# Reports hub grouping, in display order.
REPORT_GROUPS = [
    ('Inventory', 'fas fa-boxes-stacked', ['stock', 'valuation', 'batches', 'ledger']),
    ('Cost', 'fas fa-indian-rupee-sign', ['variance', 'consumption']),
    ('Purchase', 'fas fa-cart-shopping', ['purchases']),
    ('Expiry', 'fas fa-hourglass-half', ['expiry']),
]
