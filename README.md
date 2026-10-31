# Panel de seguimiento · Cartera BETA0

Página web (GitHub Pages) que se actualiza sola con los valores liquidativos de Finect,
el tipo de cambio del BCE y dos ETF de referencia (MSCI World y S&P 500).

## Cómo funciona
1. `.github/workflows/update.yml` se ejecuta tres veces al día (lunes a sábado) y lanza `scripts/update.py`.
2. `scripts/update.py` lee la ficha de Finect de cada activo (`config.json`), convierte los USD a EUR con el
   tipo de referencia del BCE de la fecha del dato y guarda el resultado en `data.json`.
3. `index.html` lee `data.json` y calcula rentabilidad, riesgo, señales y comparación con el mercado.

## Puesta en marcha
1. Crear un repositorio en GitHub y subir todo el contenido de esta carpeta (incluida `.github`).
2. Settings > Actions > General > Workflow permissions: marcar **Read and write permissions**.
3. Settings > Pages > Build and deployment: **Deploy from a branch**, rama `main`, carpeta `/ (root)`.
4. Pestaña Actions > "Actualizar datos de la cartera" > **Run workflow**. Revisar el registro.
5. Abrir `https://TU_USUARIO.github.io/NOMBRE_DEL_REPO/`.

## Mantenimiento
- Cambiar la cartera, los enlaces o los índices: editar `config.json` y los activos de `data.json`.
- Si el registro muestra "no se encontro el valor liquidativo", Finect habrá cambiado su página: ajustar la
  expresión regular `RE` de `scripts/update.py`.
- Finect solo muestra el último valor; si el flujo no corre un día, ese día solo se recupera a mano.
- Los datos son una aproximación: el valor oficial lo calcula ISEFI con Bloomberg.
