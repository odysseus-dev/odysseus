// Centralized base path utility for Odysseus
// This file should be included before any other scripts that use navigation

(function() {
  // Derive the base path from the current URL pathname
  // e.g., '/odysseus/login' -> '/odysseus', '/odysseus/' -> '/odysseus'
  function getBasePath() {
    const pathname = window.location.pathname;
    
    // All known frontend page routes in Odysseus
    const routes = [
      '/login', '/compare', '/monitor', '/settings',
      '/calendar', '/notes', '/cookbook', '/email',
      '/memory', '/gallery', '/tasks', '/library'
    ];
    
    for (const r of routes) {
      if (pathname.endsWith(r)) {
        return pathname.substring(0, pathname.length - r.length);
      }
      if (pathname.includes(r + '/')) {
        return pathname.substring(0, pathname.indexOf(r));
      }
    }
    
    // Remove trailing slashes, then check if we are at root or subdirectory
    const withoutTrailing = pathname.replace(/\/+$/, '');
    return (withoutTrailing && withoutTrailing !== '/') ? withoutTrailing : '';
  }

  // Also provide a full base URL (origin + base path)
  function getBaseURL() {
    return window.location.origin + getBasePath();
  }

  // Expose globally
  window.__odysseusBasePath = getBasePath();
  window.__odysseusBaseURL = getBaseURL();
})();