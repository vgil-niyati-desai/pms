import { useCallback, useEffect, useState } from "react";
import {
  ACCOUNT_ROLES,
  createUser,
  listUsers,
  revokeUserSessions,
  updateUser,
} from "../../api/users";
import { useAuth } from "../../app/AuthContext";

const EMPTY_ACCOUNT = { username: "", password: "", role: "Normal User" };

function accountDraft(user) {
  return { role: user.role, is_active: user.is_active };
}

export default function UserManagementPage() {
  const { user: currentUser } = useAuth();
  const [users, setUsers] = useState([]);
  const [drafts, setDrafts] = useState({});
  const [account, setAccount] = useState(EMPTY_ACCOUNT);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const loadUsers = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const records = await listUsers();
      setUsers(records);
      setDrafts(Object.fromEntries(records.map((record) => [record.id, accountDraft(record)])));
    } catch (requestError) {
      setError(requestError.message || "Could not load user accounts.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadUsers();
  }, [loadUsers]);

  function updateDraft(userId, changes) {
    setDrafts((previous) => ({
      ...previous,
      [userId]: { ...previous[userId], ...changes },
    }));
  }

  async function submitNewAccount(event) {
    event.preventDefault();
    setBusy("create");
    setError("");
    setNotice("");
    try {
      await createUser(account);
      setAccount(EMPTY_ACCOUNT);
      setNotice("Account created. Share its temporary password securely.");
      await loadUsers();
    } catch (requestError) {
      setError(requestError.message || "Could not create the account.");
    } finally {
      setBusy("");
    }
  }

  async function saveAccount(userId) {
    setBusy(userId);
    setError("");
    setNotice("");
    try {
      const updated = await updateUser(userId, drafts[userId]);
      if (userId === currentUser.id && (!updated.is_active || updated.role !== "Admin")) {
        window.dispatchEvent(new Event("pms:auth-expired"));
        return;
      }
      setNotice(`Updated ${updated.username}.`);
      await loadUsers();
    } catch (requestError) {
      setError(requestError.message || "Could not update the account.");
    } finally {
      setBusy("");
    }
  }

  async function revokeSessions(userId) {
    setBusy(`sessions-${userId}`);
    setError("");
    setNotice("");
    try {
      await revokeUserSessions(userId);
      setNotice("Sessions revoked. The account must sign in again.");
      if (userId === currentUser.id) {
        window.dispatchEvent(new Event("pms:auth-expired"));
      }
    } catch (requestError) {
      setError(requestError.message || "Could not revoke sessions.");
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="list-layout">
      <header>
        <h2>User management</h2>
        <p className="muted">Create accounts, assign roles, control access, and revoke sessions.</p>
      </header>

      {error && <div className="alert alert-error" role="alert">{error}</div>}
      {notice && <div className="alert alert-success" role="status">{notice}</div>}

      <section className="card">
        <h3>Create account</h3>
        <form onSubmit={submitNewAccount}>
          <div className="form-grid">
            <label>
              Username or email
              <input
                autoComplete="username"
                required
                maxLength={254}
                value={account.username}
                onChange={(event) => setAccount({ ...account, username: event.target.value })}
              />
            </label>
            <label>
              Initial password (at least 12 characters)
              <input
                autoComplete="new-password"
                type="password"
                required
                minLength={12}
                maxLength={1024}
                value={account.password}
                onChange={(event) => setAccount({ ...account, password: event.target.value })}
              />
            </label>
            <label>
              Role
              <select
                value={account.role}
                onChange={(event) => setAccount({ ...account, role: event.target.value })}
              >
                {ACCOUNT_ROLES.map((role) => <option key={role}>{role}</option>)}
              </select>
            </label>
          </div>
          <button className="btn btn-primary" type="submit" disabled={busy === "create"}>
            {busy === "create" ? "Creating…" : "Create account"}
          </button>
        </form>
      </section>

      <section className="card">
        <h3>Accounts</h3>
        {loading ? (
          <p role="status">Loading accounts…</p>
        ) : users.length === 0 ? (
          <p className="muted">No accounts found.</p>
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Username</th>
                  <th>Role</th>
                  <th>Active</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {users.map((record) => (
                  <tr key={record.id}>
                    <td>{record.username}{record.id === currentUser.id ? " (you)" : ""}</td>
                    <td>
                      <select
                        aria-label={`Role for ${record.username}`}
                        value={drafts[record.id]?.role ?? record.role}
                        onChange={(event) => updateDraft(record.id, { role: event.target.value })}
                      >
                        {ACCOUNT_ROLES.map((role) => <option key={role}>{role}</option>)}
                      </select>
                    </td>
                    <td>
                      <input
                        aria-label={`Active status for ${record.username}`}
                        type="checkbox"
                        checked={drafts[record.id]?.is_active ?? false}
                        onChange={(event) => updateDraft(record.id, { is_active: event.target.checked })}
                      />
                    </td>
                    <td className="table-actions">
                      <button
                        className="btn btn-sm"
                        type="button"
                        disabled={Boolean(busy)}
                        onClick={() => saveAccount(record.id)}
                      >
                        Save
                      </button>
                      <button
                        className="btn btn-sm"
                        type="button"
                        disabled={Boolean(busy)}
                        onClick={() => revokeSessions(record.id)}
                      >
                        Revoke sessions
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
