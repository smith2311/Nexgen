# MeriTrack — Stitch concept mockups

Salvaged from the untracked `stitch_meritrack_redisgn/` export before it was deleted
(2026-08-06). These are the only unique files it contained; its 36 CSS/HTML files were
byte-identical to what already ships in `static/meritrack/css/` and `templates/meritrack/`
(applied in commit `1ec617d8`).

**None of these concepts were adopted.** They are kept as reference only. Every one uses a
left-sidebar shell, whereas the shipped UI uses a top hero header (`.mtd-header`) with a
mobile bottom nav — so none can be lifted directly.

| File | Depicts | Exists in app? |
| --- | --- | --- |
| `meritrack_design_tokens_ui_kit.png` | Palette, type scale, button/badge states | Superseded — specifies `#0056b3` / `#28a745`; app uses `#0062cc` / `#059669` (see `meritrack-core.css` `:root`) |
| `meritrack_enterprise_dashboard_v1.png` | Dark glassmorphic dashboard, sidebar | No — app dashboard is light-themed |
| `meritrack_inventory_master_v2.png` | Inventory table, icon rail, pagination | Page exists (`inventory.html`), layout differs |
| `procurement_management_dashboard.png` | Purchase requisitions, budget-vs-spend | No such app |
| `settings_team_management_1.png` / `_2.png` | User roles + permissions admin (near-duplicates) | No such page |
| `supply_chain_analytics_heatmaps.png` | Shipment world map, stock-out risk heatmap | No — `supply_chain_tracker.css` exists but has no template or view |
| `warehouse_scanner_interface.png` | Three-pane pick/scan/keypad console | Page exists (`scanner.html`), layout differs |

Note: `static/meritrack/css/` also holds `forecasting_dashboard.css`, `insights_dashboard.css`,
`sap_integration.css`, `supply_chain_tracker.css` and the three `reconciliation_*.css` files,
none of which are linked from any template. The reconciliation ones are stale duplicates whose
selectors no longer match their rewritten templates; the rest have no view at all.
