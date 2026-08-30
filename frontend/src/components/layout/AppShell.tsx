import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { useAuth } from "@/auth/AuthContext";
import { Button } from "@/components/ui/Button";
import {
  DashboardIcon,
  LedgerIcon,
  LogoutIcon,
  MenuIcon,
  MoonIcon,
  SunIcon,
  WalletIcon,
} from "@/components/ui/icons";
import { useTheme } from "@/hooks/useTheme";
import styles from "./layout.module.css";

const NAV_ITEMS = [
  { to: "/", label: "Dashboard", icon: DashboardIcon, end: true },
  { to: "/transactions", label: "Transactions", icon: LedgerIcon, end: false },
];

const PAGE_TITLES: Record<string, string> = {
  "/": "Dashboard",
  "/transactions": "Transactions",
};

export function AppShell(): JSX.Element {
  const { user, logout } = useAuth();
  const { theme, toggle } = useTheme();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);

  // Close the slide-over on navigation. Without this, tapping a link on a phone
  // changes the page behind a menu that stays open covering it — which reads as
  // the tap not having worked.
  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname]);

  const initial = user?.email?.[0] ?? "?";

  return (
    <div className={styles.shell}>
      {/* The scrim only exists while the mobile menu is open. Rendering it
          always and hiding it with CSS would leave an invisible element
          swallowing clicks on desktop. */}
      {menuOpen && (
        <div
          className={styles.scrim}
          onClick={() => setMenuOpen(false)}
          aria-hidden="true"
        />
      )}

      <aside className={`${styles.sidebar} ${menuOpen ? styles.sidebarOpen : ""}`}>
        <div className={styles.brand}>
          <span className={styles.brandMark}>
            <WalletIcon size={16} />
          </span>
          Finance
        </div>

        <nav className={styles.nav}>
          {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              // `NavLink` gives the active state as a function argument rather
              // than requiring a manual comparison against the location — which
              // is the version that breaks on nested routes and trailing
              // slashes.
              className={({ isActive }) =>
                `${styles.navLink} ${isActive ? styles.navLinkActive : ""}`
              }
            >
              <Icon size={17} />
              {label}
            </NavLink>
          ))}
        </nav>

        <div className={styles.sidebarFooter}>
          <div className={styles.userRow}>
            <span className={styles.avatar}>{initial}</span>
            <span className={styles.userEmail} title={user?.email}>
              {user?.email}
            </span>
          </div>
          <Button variant="ghost" onClick={logout} fullWidth>
            <LogoutIcon size={16} />
            Sign out
          </Button>
        </div>
      </aside>

      <header className={styles.topbar}>
        <div className={styles.topbarActions}>
          <Button
            className={styles.menuButton}
            variant="ghost"
            iconOnly
            onClick={() => setMenuOpen((open) => !open)}
            aria-label={menuOpen ? "Close menu" : "Open menu"}
            aria-expanded={menuOpen}
          >
            <MenuIcon size={18} />
          </Button>
          <h1 className={styles.pageTitle}>
            {PAGE_TITLES[location.pathname] ?? "Finance"}
          </h1>
        </div>

        <div className={styles.topbarActions}>
          <Button
            variant="ghost"
            iconOnly
            onClick={toggle}
            // The label states what the button *will do*, not what the current
            // theme is. "Dark mode" as a label on a toggle is ambiguous about
            // whether it describes the state or the action.
            aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
            title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
          >
            {theme === "dark" ? <SunIcon size={17} /> : <MoonIcon size={17} />}
          </Button>
        </div>
      </header>

      <main className={styles.main}>
        <Outlet />
      </main>
    </div>
  );
}
