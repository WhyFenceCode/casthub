// Shared between the logout screens on every monitor: a key or click on one stops the
// countdown on all of them.
.pragma library

var callbacks = [];
function onCancel(cb) { callbacks.push(cb); }
function cancelAll() { callbacks.forEach(function (c) { if (c) c(); }); }
