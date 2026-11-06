const util = require("util");

// Workaround: Node >=20 expone util.styleText con firma
// styleText(format | format[], text); algunas versiones de
// @expo/cli le pasan un array de formatos y rompen el arranque
// de Metro. Reducimos el array a su primer formato.
if (util.styleText) {
  const original = util.styleText;
  util.styleText = function (format, text) {
    if (Array.isArray(format)) {
      format = format[0];
    }
    return original.call(util, format, text);
  };
}

const { getDefaultConfig } = require("@expo/metro-config");

module.exports = (async () => {
  const config = await getDefaultConfig(__dirname);
  return config;
})();
