/**
 * Incident-demo track (lab-only) — frontend constants.
 *
 * Mirrors `backend/app/vnext/interview/incident.py`: the same seed code + task
 * prompt are shown in the room (code box preload + task card) while the backend
 * drives the incident-shaped interviewer turns and rubric. Kept as a tiny data
 * module so the room never reaches into backend internals.
 */

export const INCIDENT_TRACK = "incident-demo";

/** Buggy payments snippet preloaded into the candidate's code box. */
export const INCIDENT_SEED_CODE = `# Payments service — charge endpoint (Python).
# Reported: customers are occasionally charged twice when the provider times out
# and the client retries the request. Find and fix the failure mode.

def charge_customer(db, provider, customer_id, amount_cents, idempotency_key):
    # 1) has this charge already been made?
    rows = db.query(
        "SELECT id, provider_ref FROM charges "
        "WHERE customer_id = %s AND amount_cents = %s",
        customer_id, amount_cents,
    )
    if rows:
        return rows[0]

    # 2) call the payment provider (may time out; the client then retries)
    result = provider.charge(customer_id, amount_cents)

    # 3) record the charge
    db.execute(
        "INSERT INTO charges (customer_id, amount_cents, provider_ref) "
        "VALUES (%s, %s, %s)",
        customer_id, amount_cents, result.provider_ref,
    )
    return result
`;

/** One-line scenario shown as the task card near the code box. */
export const INCIDENT_TASK_PROMPT =
  "Production issue: this payment API sometimes creates DUPLICATE charges when " +
  "the provider times out and the client retries. Inspect the code in the box, " +
  "find the failure mode, and make a retry with the same idempotency key safe.";

/** Sensible intake defaults so a human never has to think about role/JD setup. */
export const INCIDENT_DEFAULTS = {
  role: "Backend Engineer",
  seniority: "senior" as const,
  languages: "python",
  durationMinutes: 25,
  jobDescription:
    "Backend engineer on a payments platform. You own the charge/refund APIs: " +
    "idempotent retries, exactly-once semantics, transaction boundaries, and " +
    "concurrency under load. Strong Python, SQL, and distributed-systems judgment " +
    "(locks, unique constraints, provider idempotency keys). On-call for duplicate " +
    "charges, race conditions, and provider timeouts.",
};

/** The canonical human entry URL for the incident demo (no param knowledge needed). */
export const INCIDENT_INTAKE_URL = "/lab/interview-v3/intake?adapter=live-llm&track=incident-demo";

/** Normalize a query-param track value to a known track (or undefined). */
export function normalizeTrack(value: string | null | undefined): string | undefined {
  return value === INCIDENT_TRACK ? INCIDENT_TRACK : undefined;
}
