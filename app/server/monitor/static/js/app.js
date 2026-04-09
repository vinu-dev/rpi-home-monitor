/**
 * Home Monitor OS — Dashboard JavaScript
 *
 * Provides API communication, authentication state management,
 * toast notifications, loading overlay, and utility functions.
 * All page-specific scripts rely on the window.HM namespace.
 */
(function() {
    'use strict';

    var _csrfToken = '';
    var _user = null;

    /* ============================================================
       API — fetch wrapper with credentials and CSRF
       ============================================================ */
    var api = {
        get: function(url) {
            return _request('GET', url, null);
        },
        post: function(url, body) {
            return _request('POST', url, body);
        },
        put: function(url, body) {
            return _request('PUT', url, body);
        },
        del: function(url) {
            return _request('DELETE', url, null);
        },
    };

    function _request(method, url, body) {
        var opts = {
            method: method,
            credentials: 'same-origin',
            headers: {},
        };

        if (body !== null && body !== undefined) {
            opts.headers['Content-Type'] = 'application/json';
            opts.body = JSON.stringify(body);
        }

        // Include CSRF token on state-changing requests
        if (method !== 'GET' && _csrfToken) {
            opts.headers['X-CSRF-Token'] = _csrfToken;
        }

        return fetch(url, opts).then(function(resp) {
            if (resp.status === 401) {
                // Session expired — redirect to login
                var path = window.location.pathname;
                if (path !== '/login' && path !== '/setup') {
                    window.location.href = '/login';
                }
                return Promise.reject(new Error('Authentication required'));
            }

            return resp.json().then(function(data) {
                if (!resp.ok) {
                    var msg = data.error || data.message || 'Request failed';
                    return Promise.reject(new Error(msg));
                }
                return data;
            });
        });
    }

    /* ============================================================
       Auth — session and user state
       ============================================================ */
    var auth = {
        setCsrfToken: function(token) {
            _csrfToken = token || '';
        },

        getCsrfToken: function() {
            return _csrfToken;
        },

        setUser: function(user) {
            _user = user || null;
        },

        getUser: function() {
            return _user;
        },

        getMe: function() {
            return api.get('/api/v1/auth/me').then(function(data) {
                _csrfToken = data.csrf_token || '';
                _user = data.user || null;
                return data;
            });
        },

        logout: function() {
            return api.post('/api/v1/auth/logout', {}).then(function() {
                _csrfToken = '';
                _user = null;
                window.location.href = '/login';
            }).catch(function() {
                window.location.href = '/login';
            });
        },
    };

    /* ============================================================
       Toast Notifications
       ============================================================ */
    function toast(message, type) {
        type = type || 'info';
        var container = document.getElementById('toast-container');
        if (!container) return;

        var el = document.createElement('div');
        el.className = 'toast toast--' + type;
        el.textContent = message;
        container.appendChild(el);

        setTimeout(function() {
            el.style.animation = 'toast-out 0.3s ease-in forwards';
            setTimeout(function() {
                if (el.parentNode) el.parentNode.removeChild(el);
            }, 300);
        }, 3500);
    }

    /* ============================================================
       Loading Overlay
       ============================================================ */
    function showLoading() {
        var el = document.getElementById('loading-overlay');
        if (el) el.classList.remove('hidden');
    }

    function hideLoading() {
        var el = document.getElementById('loading-overlay');
        if (el) el.classList.add('hidden');
    }

    /* ============================================================
       Utility Functions
       ============================================================ */
    function formatBytes(bytes) {
        if (bytes === 0 || bytes === null || bytes === undefined) return '0 B';
        var units = ['B', 'KB', 'MB', 'GB', 'TB'];
        var i = Math.floor(Math.log(bytes) / Math.log(1024));
        if (i >= units.length) i = units.length - 1;
        var value = bytes / Math.pow(1024, i);
        return value.toFixed(i === 0 ? 0 : 1) + ' ' + units[i];
    }

    function formatDuration(seconds) {
        if (seconds === null || seconds === undefined) return '--';
        seconds = Math.round(seconds);
        var m = Math.floor(seconds / 60);
        var s = seconds % 60;
        return m + 'm ' + s + 's';
    }

    function formatTemp(celsius) {
        if (celsius === null || celsius === undefined) return '--';
        return parseFloat(celsius).toFixed(1) + '\u00B0C';
    }

    function escapeHtml(str) {
        if (!str) return '';
        var div = document.createElement('div');
        div.appendChild(document.createTextNode(str));
        return div.innerHTML;
    }

    /* ============================================================
       Navigation Highlighting
       ============================================================ */
    function highlightNav() {
        var nav = document.getElementById('bottom-nav');
        if (!nav) return;

        var path = window.location.pathname.replace(/\/$/, '') || '/dashboard';
        var items = nav.querySelectorAll('.bottom-nav__item');

        items.forEach(function(item) {
            item.classList.remove('active');
            var page = item.getAttribute('data-page');
            if (path === '/' + page || path.indexOf('/' + page) === 0) {
                item.classList.add('active');
            }
        });
    }

    /* ============================================================
       Initialization
       ============================================================ */
    function initApp() {
        var path = window.location.pathname;

        // Skip auth check on login and setup pages
        if (path === '/login' || path === '/setup') {
            highlightNav();
            return;
        }

        // Check setup status
        api.get('/api/v1/setup/status')
            .then(function(data) {
                if (!data.setup_complete) {
                    window.location.href = '/setup';
                    return;
                }
                return checkAuth();
            })
            .catch(function() {
                // If setup endpoint fails, still try auth
                return checkAuth();
            });
    }

    function checkAuth() {
        return auth.getMe().then(function(data) {
            // Update top bar username
            var usernameEl = document.getElementById('topbar-username');
            if (usernameEl && _user) {
                usernameEl.textContent = _user.username;
            }

            highlightNav();
        }).catch(function() {
            // 401 is handled by _request — will redirect to login
        });
    }

    // Bind logout button
    document.addEventListener('DOMContentLoaded', function() {
        var btnLogout = document.getElementById('btn-logout');
        if (btnLogout) {
            btnLogout.addEventListener('click', function() {
                auth.logout();
            });
        }
    });

    // Run initialization
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initApp);
    } else {
        initApp();
    }

    /* ============================================================
       Public API — exposed on window.HM
       ============================================================ */
    window.HM = {
        api: api,
        auth: auth,
        toast: toast,
        showLoading: showLoading,
        hideLoading: hideLoading,
        formatBytes: formatBytes,
        formatDuration: formatDuration,
        formatTemp: formatTemp,
        escapeHtml: escapeHtml,
    };

})();
