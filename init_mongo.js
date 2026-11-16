// Datos de prueba del BASELINE INSEGURO.
// Contraseñas en claro a propósito: forma parte de las malas prácticas a evidenciar.
db = db.getSiblingDB("tienda");

db.usuarios.insertMany([
  { username: "admin", password: "Adm1n!2026", rol: "admin",   email: "admin@tienda.lab" },
  { username: "alice", password: "alice123",   rol: "cliente", email: "alice@tienda.lab" },
  { username: "bob",   password: "bob123",     rol: "cliente", email: "bob@tienda.lab" }
]);

db.pedidos.insertMany([
  { producto: "Teclado mecánico", cantidad: 1, cliente: "alice" },
  { producto: "Ratón inalámbrico", cantidad: 2, cliente: "bob" },
  { producto: "Monitor 27\"",      cantidad: 1, cliente: "alice" }
]);
