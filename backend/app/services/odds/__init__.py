"""Auditoría de cuotas: snapshot de cuotas de mercado por evento.

Mismo patrón que `app.services.results` pero para odds: proveedores
con protocolo común, cuota persistente en `provider_state.json` (vía
`results.base`) y un job periódico (`snapshotter`) que guarda en
`odds_snapshots` la curva de cada mercado — apertura, precio cercano
a la publicación y cierre — para compararla con la cuota del tipster.
"""
