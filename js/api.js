/* I Love Tools - shared helper for calling the Python backend.
   All tools call functions here via fetch() to the Vercel-hosted API. */
var ILOVETOOLS_API = (function () {
  // Change this to your deployed Vercel backend URL in production.
  var BASE = "https://tools-site-backend.vercel.app";

  function uploadFile(url, file, extra) {
    var fd = new FormData();
    fd.append(Object.keys(extra.fields || {})[0] || "file", file);
    var fields = extra.fields || {};
    var firstFileKey = Object.keys(fields)[0] || "file";
    // append the file under its field name
    fd = new FormData();
    fd.append(firstFileKey, file);
    Object.keys(fields).forEach(function (k) {
      if (k !== firstFileKey) fd.append(k, fields[k]);
    });
    return fetch(BASE + url, { method: "POST", body: fd });
  }

  function triggerDownload(blob, filename) {
    var a = document.createElement("a");
    var url = URL.createObjectURL(blob);
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  return {
    base: BASE,
    uploadFile: uploadFile,
    triggerDownload: triggerDownload,
    // Generic POST returning parsed JSON
    postJson: function (url, body) {
      return fetch(BASE + url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      }).then(function (r) { return r.json(); });
    }
  };
})();
