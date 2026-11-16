// Inicialización del ENTORNO SEGURO.
// Se ejecuta una sola vez (volumen vacío) autenticado como el usuario root
// definido en MONGO_INITDB_ROOT_USERNAME/PASSWORD.
const tienda = db.getSiblingDB("tienda");

// Mínimo privilegio: la aplicación solo puede leer/escribir en "tienda".
tienda.createUser({
  user: process.env.MONGO_APP_USER,
  pwd: process.env.MONGO_APP_PASSWORD,
  roles: [{ role: "readWrite", db: "tienda" }]
});

// Contraseñas almacenadas como PBKDF2-SHA256 (310 000 iteraciones, sal aleatoria).
// Las credenciales de prueba son las mismas que en el baseline.
tienda.usuarios.insertMany([
  { username: "admin", rol: "admin",   email: "admin@tienda.lab",
    password_hash: "pbkdf2_sha256$310000$adfa3b5a545894321fda2033de9af816$7eec10986479e5013e65e55ccdf64a10a06e3378f36bd9263b3d4991ad65581c" },
  { username: "alice", rol: "cliente", email: "alice@tienda.lab",
    password_hash: "pbkdf2_sha256$310000$ad999f13f543220efdbc58ab3b55044d$f393ac3bb798b129781907a0ea8d37e67073e5377011636cdf9c9f20c5f83950" },
  { username: "bob",   rol: "cliente", email: "bob@tienda.lab",
    password_hash: "pbkdf2_sha256$310000$449b4ff106b4daf70b1a2ba5fa9292d6$e422b4b0752dd67d73fe9608c728cf2dadd50f45f2553f815029f72ae87dcfec" }
]);
tienda.usuarios.createIndex({ username: 1 }, { unique: true });

tienda.pedidos.insertMany([
  { producto: "Teclado mecánico", cantidad: 1, cliente: "alice" },
  { producto: "Ratón inalámbrico", cantidad: 2, cliente: "bob" },
  { producto: "Monitor 27\"",      cantidad: 1, cliente: "alice" }
]);
