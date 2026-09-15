const util = require("util");

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
