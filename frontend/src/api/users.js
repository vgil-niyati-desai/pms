import { requestJson, requestNoContent } from "./http";

export const ACCOUNT_ROLES = ["Admin", "HOD", "Normal User"];

export function listUsers() {
  return requestJson("/auth/users", { fallback: "Failed to load user accounts" });
}

export function createUser(account) {
  return requestJson("/auth/users", {
    method: "POST",
    body: JSON.stringify(account),
    fallback: "Failed to create the account",
  });
}

export function updateUser(userId, changes) {
  return requestJson(`/auth/users/${userId}`, {
    method: "PATCH",
    body: JSON.stringify(changes),
    fallback: "Failed to update the account",
  });
}

export function revokeUserSessions(userId) {
  return requestNoContent(`/auth/users/${userId}/sessions`, {
    method: "DELETE",
    fallback: "Failed to revoke sessions",
  });
}
