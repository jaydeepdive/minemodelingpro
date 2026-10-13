// MMP Studio — browser configuration.
// These two values are PUBLIC by design (the anon key only ever acts as the
// signed-in user, through the row-level-security policies in studio/schema.sql).
// The service-role key must never appear here — it belongs to the worker only.
window.MMP_CONFIG = {
  SUPABASE_URL: "",          // https://<project-ref>.supabase.co
  SUPABASE_ANON_KEY: "",     // the "anon public" key from Supabase → Project Settings → API
  SITE_NAME: "MineModelingPro Studio",
  MAX_FILE_MB: 200,
  // formats the importer understands
  ACCEPT: ".csv,.tsv,.txt,.xlsx,.xlsm,.xls,.mdb,.accdb,.zip,.parquet,.json,.dbf",
};
