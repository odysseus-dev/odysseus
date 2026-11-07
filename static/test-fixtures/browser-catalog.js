const products = [
  { name: 'Cedar', color: 'orange', price: 219 },
  { name: 'Harbor', color: 'orange', price: 349 },
  { name: 'Willow', color: 'yellow', price: 279 },
];
if (new URLSearchParams(location.search).get('overlay') === 'delayed') {
  const dialog = document.getElementById('cookie-choices');
  document.getElementById('query').addEventListener('input', () => dialog.showModal(), { once: true });
  for (const choice of ['reject', 'accept']) {
    document.getElementById(`${choice}-optional`).addEventListener('click', () => {
      document.getElementById('consent-state').textContent = `Optional cookies: ${choice === 'reject' ? 'rejected' : 'accepted'}`;
      dialog.close();
    });
  }
}
document.getElementById('search').addEventListener('submit', event => {
  event.preventDefault();
  const query = document.getElementById('query').value.toLowerCase();
  const matches = products.filter(p => query.includes(p.color) || query.includes(p.name.toLowerCase()));
  const results = document.getElementById('results');
  results.replaceChildren();
  for (const product of matches) {
    const row = document.createElement('p');
    row.textContent = `${product.name}: ${product.color} sofa, $${product.price}`;
    results.append(row);
  }
  if (!matches.length) results.textContent = 'No matching sofas.';
});
