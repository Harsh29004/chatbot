/**
 * Staff sign-in: `POST /api/admin/login`.
 *
 * Port of `backend/admin/login_router.py`.
 *
 * Kept off the admin router on purpose. That router declares the admin-key
 * check once for every route, and the one endpoint that hands out credentials
 * obviously can't require them.
 */

import { Router } from "express";
import { z } from "zod";

import {
  asyncHandler,
  clientAddress,
  parseBody,
  serviceUnavailable,
  tooManyRequests,
  unauthorized,
} from "../shared/http.js";
import * as session from "./session.js";

export const router = Router();

const adminLoginSchema = z.object({
  username: z.string().min(1).max(200),
  password: z.string().min(1).max(200),
});

router.post(
  "/api/admin/login",
  asyncHandler(async (req, res) => {
    const body = parseBody(adminLoginSchema, req.body);

    if (!session.passwordLoginEnabled()) {
      throw serviceUnavailable(
        "Admin sign-in isn't set up. Set ADMIN_PASSWORD on the server.",
      );
    }

    const client = clientAddress(req);
    if (await session.isLockedOut(client)) {
      throw tooManyRequests("Too many failed sign-ins. Try again in 15 minutes.");
    }

    if (!session.credentialsMatch(body.username, body.password)) {
      await session.recordFailure(client);
      // One message for a wrong username and a wrong password alike.
      throw unauthorized("Wrong username or password.");
    }

    await session.clearFailures(client);
    const { token, expiresAt } = session.issueToken();
    res.json({ token, expires_at: expiresAt });
  }),
);
