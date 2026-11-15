// Row action menus on the data tables (app.css, "Row actions").
// A <details class="row-menu"> opens a list placed in fixed position under its
// button, flipped above it near the bottom of the window, so the table's scroll
// box cannot clip it. One open at a time; a click elsewhere, Escape or a scroll
// closes it.
(function () {
    function openMenus() { return document.querySelectorAll('details.row-menu[open]'); }
    function closeAll(except) { openMenus().forEach(function (d) { if (d !== except) d.open = false; }); }

    function place(d) {
        var button = d.querySelector('summary'), list = d.querySelector('.row-menu-list');
        if (!button || !list) return;
        var r = button.getBoundingClientRect();
        list.style.top = (r.bottom + 4) + 'px';
        list.style.left = Math.max(8, r.right - list.offsetWidth) + 'px';
        if (list.getBoundingClientRect().bottom > window.innerHeight - 8) {
            list.style.top = Math.max(8, r.top - 4 - list.offsetHeight) + 'px';
        }
    }

    // toggle does not bubble; a capturing listener still sees it.
    document.addEventListener('toggle', function (e) {
        var d = e.target;
        if (!d.classList || !d.classList.contains('row-menu') || !d.open) return;
        closeAll(d);
        place(d);
    }, true);

    document.addEventListener('click', function (e) {
        openMenus().forEach(function (d) {
            if (!d.contains(e.target)) { d.open = false; return; }
            // An item that opens a dialog closes the menu behind it.
            var item = e.target.closest('.row-menu-item');
            if (item && item.tagName === 'BUTTON' && !item.closest('form')) d.open = false;
        });
    });

    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape') return;
        openMenus().forEach(function (d) { d.open = false; d.querySelector('summary').focus(); });
    });

    document.addEventListener('scroll', function () { closeAll(null); }, true);
    window.addEventListener('resize', function () { closeAll(null); });
})();
