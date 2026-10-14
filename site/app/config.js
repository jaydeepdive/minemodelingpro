// MMP Studio — browser configuration.
// Studio talks to this site's own API with a session cookie, so there are no keys
// here at all: the database and the object store are reachable only by the server.
window.MMP_CONFIG = {
  API_BASE: "/api",
  SITE_NAME: "MineModelingPro Studio",
  MAX_FILE_MB: 200,
  // formats the importer understands
  ACCEPT: ".csv,.tsv,.txt,.xlsx,.xlsm,.xls,.mdb,.accdb,.zip,.parquet,.json,.dbf",
};
