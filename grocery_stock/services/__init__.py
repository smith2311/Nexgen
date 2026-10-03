"""
Grocery Stock & Cost Management — business logic.

Views validate input and call into here; nothing in this package imports from
views or serializers. Keeping the logic callable from a view, a management
command, a Celery task and a test is the whole point of the split.

Populated in the phases after masters:
    ledger.py       append-only stock_transactions writer + balance rebuild
    landed_cost.py  header charge allocation across receipt lines
    valuation.py    weighted average and FIFO
    post_grn.py     the one atomic goods-receipt transaction
"""
