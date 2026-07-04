/* Applies the saved theme before first paint to avoid a flash of the wrong mode.
   Loaded synchronously in <head> on every page. */
(function () {
    if (localStorage.getItem('sl-theme') === 'light') {
        document.documentElement.setAttribute('data-theme', 'light');
    }
})();
