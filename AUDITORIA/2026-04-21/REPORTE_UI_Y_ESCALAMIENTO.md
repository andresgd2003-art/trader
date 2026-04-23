# Reporte: Refactorización UI y Escalamiento de Capital (Palanca 1)

**Autor**: Antigravity AI (Gemini/Claude Opus)  
**Fecha**: 2026-04-21  
**Repositorio**: `andresgd2003-art/trader`  

---

## 1. Rediseño Estético del Dashboard (UI/UX)
Se aplicó un *overhaul* visual profundo al panel central (`static/index.html`) para transformarlo en una interfaz de grado institucional, todo construido con CSS puro para evitar sobrecarga de CPU o renderizado pesado en Javascript.

### Cambios Implementados:
- **Aurora Mesh Background**: Fondo dinámico pre-renderizado vía gradientes radiales estáticos, logrando una estética "glass" moderna sin consumir CPU.
- **Glassmorphism**: Transparencias sutiles con desenfoque (`backdrop-filter: blur(12px)`) en todas las tarjetas de métricas y barras de navegación.
- **Lucide Icons**: Reemplazo total de la antigua iconografía basada en emojis (que se renderiza distinto en cada SO) por iconos vectoriales SVG (`lucide`), que permiten tematización armónica con la paleta de acentos del dashboard.
- **Pill Tabs y Progress Bars**: Formas redondeadas y un delineado con gradientes de color. Las barras de progreso ahora tienen relieve volumétrico interno y las stat-cards incluyen bordes iluminados a la izquierda.
- **Tipografía y Jerarquía (Gradient Text)**: El valor principal de *Equity* en el panel fue destacado utilizando texto con gradiente (`linear-gradient(135deg, #00e676, #00d4ff)`) para enfocar la vista del usuario.

> **Objetivo de Rendimiento Cumplido:** Pese al incremento en complejidad visual, se evitó deliberadamente el uso de *Sparklines* dinámicos en Canvas y de animaciones *count-up* que habrían saturado el ciclo de render de 15 segundos.

---

## 2. Palanca 1: Escalamiento de Capital (Sizing de Cuenta a $200)
Una vez comprobada la estabilidad técnica del motor en los logs, se calibraron los porcentajes y montos máximos asignados por trade para simular óptimamente una **Cash Account de $200 USD** (con liquidación T+1 para equities/ETFs, T+0 cripto).

### Ajustes en `engine/order_manager.py` (ETF)
Se ajustó el porcentaje del *settled_cash* para ser más agresivo pero seguro, permitiendo compras fraccionarias de los ETFs sin agotar la cuenta:
- **BULL**: 8% (Aprox. ~$16 USD por posición).
- **CHOP**: 5% (Aprox. ~$10 USD por posición).
- **BEAR/UNKNOWN**: 3% (Aprox. ~$6 USD por posición).

### Ajustes en `engine/order_manager_equities.py` (Equities)
Las *Bracket Orders* pasaron a usar un 10% del capital asentado (approx. ~$20 USD por operación) en lugar de un estático y conservador monto fijo, subiendo el tope absoluto a $200 USD para futuras capitalizaciones.

### Ajustes en `engine/order_manager_crypto.py` (Crypto)
Aprovechando el *settlement* instantáneo y el comercio 24/7 de los criptoactivos:
- **DAY_CAP_USD**: Elevado de $15.00 a **$25.00** USD (12.5% de la cuenta).
- **NIGHT_CAP_MAX_USD**: Elevado a **$50.00** USD (25% de la cuenta), para sacar más tracción fuera del horario de mercado americano.

---

## 3. Despliegue
Todos los cambios fueron validados en local, encapsulados en el commit `6ca9eb2` y subidos al branch `main` de GitHub. Posteriormente, el motor fue actualizado directamente en producción utilizando el script de despliegue (`deploy_vps.py`), reiniciando el servicio systemd (`alpacatrader.service`) exitosamente en el VPS `148.230.82.14`.
