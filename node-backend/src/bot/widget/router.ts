/**
 * Widget themes, install packages, hosted scripts, and key activation.
 *
 * Everything a customer needs to put their bot on their own site, in one of
 * seven designs, without writing a chat UI.
 *
 * The designs and the package builder live in the Python bot service, next to
 * the widget source they bake a theme into. What stays here is the part that
 * needs Node: validating the API key on activation, and knowing the *public*
 * origin a customer's website should call — the bot service only ever sees a
 * loopback address and would otherwise bake the wrong host into every install.
 */

import { Router, type Request } from "express";

import { validateApiKey } from "../../shared/apiKeys.js";
import { asyncHandler, forbidden, notFound } from "../../shared/http.js";
import { botService } from "../client.js";

export const router = Router();

// Behind a reverse proxy the request's own base URL can be the internal
// address; PUBLIC_API_ORIGIN pins the one customers' sites should call.
const PUBLIC_API_ORIGIN = (process.env.PUBLIC_API_ORIGIN ?? "").trim().replace(/\/+$/, "");

function apiBase(req: Request): string {
  if (PUBLIC_API_ORIGIN) return PUBLIC_API_ORIGIN;
  // Express has no `base_url`, so it is reassembled. `req.protocol` respects
  // X-Forwarded-Proto once `trust proxy` is set, which server.ts does.
  return `${req.protocol}://${req.get("host")}`;
}

/** The seven designs. Public — the gallery is browsable before sign-up. */
router.get(
  "/api/widget/themes",
  asyncHandler(async (_req, res) => {
    res.json(await botService.widgetThemes());
  }),
);

/**
 * The install zip for one theme.
 *
 * Public on purpose: it contains no key and no account data, only the design
 * and the API origin. The key is what activates it.
 */
router.get(
  "/api/widget/themes/:themeId/package",
  asyncHandler(async (req, res) => {
    const file = await botService.widgetPackage(req.params.themeId, apiBase(req));

    res.setHeader("Content-Type", file.contentType);
    if (file.disposition) res.setHeader("Content-Disposition", file.disposition);
    res.send(file.body);
  }),
);

/** The same script, served from here for a one-line install with no upload. */
router.get(
  "/widget/v1/:file",
  asyncHandler(async (req, res) => {
    // The original route was `/widget/v1/{theme_id}.js`; Express path params do
    // not span a literal suffix, so the `.js` is stripped here instead.
    const file = req.params.file;
    if (!file.endsWith(".js")) throw notFound("Unknown widget theme.");

    const script = await botService.widgetScript(file.slice(0, -3), apiBase(req));

    res.setHeader("Content-Type", "application/javascript; charset=utf-8");
    res.setHeader("Cache-Control", "public, max-age=300");
    res.setHeader("Access-Control-Allow-Origin", "*");
    res.send(script.body);
  }),
);

/**
 * Check a key from an installed widget. Costs no credits.
 *
 * Deliberately does not go through `verifyApiKey`: that middleware charges by
 * message length, and loading a page is not asking a question. The key check
 * stays in Node — it is the one thing here that is about *identity* rather
 * than about the bot.
 */
router.get(
  "/v1/widget/activate",
  asyncHandler(async (req, res) => {
    const raw = req.headers["x-api-key"];
    const apiKey = (Array.isArray(raw) ? raw[0] : raw) ?? "";

    const keyRecord = apiKey ? await validateApiKey(apiKey) : null;
    if (keyRecord === null) throw forbidden("Invalid or revoked API key.");

    const activation = await botService.widgetActivation(keyRecord.user_id);

    res.json({
      active: true,
      bot_ready: activation.bot_ready,
      bot_name: activation.bot_name,
      template_name: activation.template_name,
    });
  }),
);
