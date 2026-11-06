// app.json sigue siendo la config base; aquí solo se enlaza
// google-services.json: en EAS Build llega como env var file
// (GOOGLE_SERVICES_JSON = ruta temporal al secreto) porque el
// archivo real está gitignored y nunca sube al builder. En local
// se usa el archivo del working dir.
module.exports = ({ config }) => ({
  ...config,
  android: {
    ...config.android,
    googleServicesFile:
      process.env.GOOGLE_SERVICES_JSON ?? "./google-services.json",
  },
});
