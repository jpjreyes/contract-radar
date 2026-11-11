"use client";

// Sign-in. Demo mode: a user switcher (X-User header). Entra mode: Microsoft sign-in (MSAL), and every API call
// carries an access token for the API; the API verifies it and maps the person's directory groups to a role.
import { InteractionRequiredAuthError, PublicClientApplication, type AccountInfo } from "@azure/msal-browser";

export const AUTH_MODE = process.env.NEXT_PUBLIC_AUTH_MODE ?? "demo";
const CLIENT_ID = process.env.NEXT_PUBLIC_ENTRA_CLIENT_ID ?? "";
const TENANT_ID = process.env.NEXT_PUBLIC_ENTRA_TENANT_ID ?? "";
const API_SCOPE = process.env.NEXT_PUBLIC_API_SCOPE ?? "";

let pca: PublicClientApplication | null = null;
let ready: Promise<void> | null = null;

function client(): PublicClientApplication {
  if (!pca) {
    pca = new PublicClientApplication({
      auth: { clientId: CLIENT_ID, authority: `https://login.microsoftonline.com/${TENANT_ID}`, redirectUri: window.location.origin + "/" },
      cache: { cacheLocation: "sessionStorage" },
    });
    ready = pca.initialize().then(async () => {
      const result = await pca!.handleRedirectPromise();
      if (result?.account) pca!.setActiveAccount(result.account);
    });
  }
  return pca;
}

export async function account(): Promise<AccountInfo | null> {
  if (AUTH_MODE !== "entra") return null;
  const c = client();
  await ready;
  return c.getActiveAccount() ?? c.getAllAccounts()[0] ?? null;
}

export async function accessToken(): Promise<string | null> {
  if (AUTH_MODE !== "entra") return null;
  const c = client();
  await ready;
  const acct = await account();
  if (!acct) {
    await c.loginRedirect({ scopes: [API_SCOPE] });
    return null;
  }
  try {
    return (await c.acquireTokenSilent({ scopes: [API_SCOPE], account: acct })).accessToken;
  } catch (e) {
    if (e instanceof InteractionRequiredAuthError) await c.acquireTokenRedirect({ scopes: [API_SCOPE], account: acct });
    throw e;
  }
}

export async function signOut(): Promise<void> {
  if (AUTH_MODE === "entra") await client().logoutRedirect();
}
