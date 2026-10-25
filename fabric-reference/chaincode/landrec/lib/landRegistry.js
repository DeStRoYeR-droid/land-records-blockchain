'use strict';

/*
 * LandRegistry — smart contract for the LandChain network.
 *
 * Three departments share one ledger:
 *   Org1MSP  Registration   opens transfer files, anchors documents
 *   Org2MSP  Revenue        verifies documents, surveys the parcel
 *   Org3MSP  Collectorate   final human decision (approve / reject)
 *
 * Access control reads the caller's X.509 certificate on every transaction:
 *   - the MSP ID (which department issued the certificate), and
 *   - the `role` attribute embedded in the certificate by that department's
 *     Fabric CA at enrollment time.
 * Nothing about the caller's identity is taken from transaction arguments.
 *
 * Privacy: no Aadhaar numbers or documents are stored on the ledger. Owners are
 * referenced by an opaque hash (ownerRef) and documents by their SHA-256
 * fingerprint; the documents themselves stay in DigiLocker.
 *
 * Determinism: every endorsing peer must compute byte-identical results, so
 * there is no Date.now(), no Math.random(), and all times come from the
 * transaction timestamp chosen by the client.
 */

const { Contract } = require('fabric-contract-api');

const REGISTRATION = 'Org1MSP';
const REVENUE = 'Org2MSP';
const COLLECTORATE = 'Org3MSP';

const STATUS = Object.freeze({
    SUBMITTED: 'SUBMITTED',                 // Org1 clerk opened the file
    WITH_REVENUE: 'WITH_REVENUE',           // Org1 -> Org2 handoff
    DOCS_VERIFIED: 'DOCS_VERIFIED',         // Org2 fingerprints matched
    FLAGGED: 'FLAGGED',                     // Org2 fingerprints did NOT match
    SURVEYED: 'SURVEYED',                   // Org2 field survey recorded
    WITH_COLLECTORATE: 'WITH_COLLECTORATE', // Org2 -> Org3 handoff
    APPROVED: 'APPROVED',                   // Org3 approved; ownership moved
    REJECTED: 'REJECTED',                   // Org3 rejected; parcel released
});

const SURVEY_TOLERANCE_PCT = 5;
const SHA256_RE = /^[0-9a-f]{64}$/;

class LandRegistry extends Contract {

    constructor() {
        super('LandRegistry');
    }

    // =====================================================================
    // Identity helpers
    // =====================================================================

    _caller(ctx) {
        const cid = ctx.clientIdentity;
        const id = cid.getID();
        const cn = /CN=([^,/:]+)/.exec(id);
        return {
            mspId: cid.getMSPID(),
            name: cn ? cn[1] : id,
            role: cid.getAttributeValue('role') || null,
            dept: cid.getAttributeValue('dept') || null,
        };
    }

    _require(ctx, action, mspId, roles) {
        const c = this._caller(ctx);
        if (c.mspId !== mspId || !roles.includes(c.role)) {
            throw new Error(
                `ACCESS DENIED: ${action} requires an ${mspId} certificate with role=` +
                `${roles.join('|')}; caller ${c.name} holds ${c.mspId} role=${c.role || 'none'}`);
        }
        return c;
    }

    // =====================================================================
    // Ledger helpers
    // =====================================================================

    _tsToIso(ts) {
        if (!ts) {
            return null;
        }
        const rawSecs = typeof ts.getSeconds === 'function' ? ts.getSeconds() : ts.seconds;
        const nanos = typeof ts.getNanos === 'function' ? ts.getNanos() : (ts.nanos || 0);
        let secs;
        if (rawSecs !== null && typeof rawSecs === 'object') {
            secs = typeof rawSecs.toNumber === 'function' ? rawSecs.toNumber() : rawSecs.low;
        } else {
            secs = Number(rawSecs);
        }
        return new Date(secs * 1000 + Math.floor(nanos / 1e6)).toISOString();
    }

    _now(ctx) {
        return this._tsToIso(ctx.stub.getTxTimestamp());
    }

    _key(ctx, type, id) {
        return ctx.stub.createCompositeKey(type, [id]);
    }

    async _get(ctx, type, id) {
        const bytes = await ctx.stub.getState(this._key(ctx, type, id));
        if (!bytes || bytes.length === 0) {
            return null;
        }
        return JSON.parse(bytes.toString());
    }

    async _mustGet(ctx, type, id, label) {
        const obj = await this._get(ctx, type, id);
        if (!obj) {
            throw new Error(`${label} ${id} does not exist`);
        }
        return obj;
    }

    async _put(ctx, type, id, obj) {
        await ctx.stub.putState(this._key(ctx, type, id), Buffer.from(JSON.stringify(obj)));
    }

    async _history(ctx, type, id) {
        const iterator = await ctx.stub.getHistoryForKey(this._key(ctx, type, id));
        const out = [];
        try {
            let res = await iterator.next();
            while (!res.done) {
                const km = res.value;
                out.push({
                    txId: km.txId,
                    timestamp: this._tsToIso(km.timestamp),
                    isDelete: !!km.isDelete,
                    value: km.value && km.value.length ? JSON.parse(km.value.toString()) : null,
                });
                res = await iterator.next();
            }
        } finally {
            await iterator.close();
        }
        return out;
    }

    async _scan(ctx, type) {
        const iterator = await ctx.stub.getStateByPartialCompositeKey(type, []);
        const out = [];
        try {
            let res = await iterator.next();
            while (!res.done) {
                out.push(JSON.parse(res.value.value.toString()));
                res = await iterator.next();
            }
        } finally {
            await iterator.close();
        }
        return out;
    }

    _mustStatus(app, allowed) {
        if (!allowed.includes(app.status)) {
            throw new Error(
                `Application ${app.appId} is ${app.status}; this step needs ${allowed.join(' or ')}`);
        }
    }

    _trail(ctx, app, caller, action) {
        app.updatedAt = this._now(ctx);
        app.trail.push({
            at: app.updatedAt,
            by: caller.name,
            org: caller.mspId,
            role: caller.role,
            action,
            status: app.status,
            txId: ctx.stub.getTxID(),
        });
    }

    _emit(ctx, name, payload) {
        ctx.stub.setEvent(name, Buffer.from(JSON.stringify(payload)));
    }

    _requireText(value, field) {
        if (typeof value !== 'string' || value.trim() === '') {
            throw new Error(`${field} is required`);
        }
        return value.trim();
    }

    _requireNumber(value, field, { min = 0, allowZero = true } = {}) {
        const n = Number(value);
        if (!Number.isFinite(n) || n < min || (!allowZero && n === 0)) {
            throw new Error(`${field} must be a ${allowZero ? 'non-negative' : 'positive'} number, got "${value}"`);
        }
        return n;
    }

    // =====================================================================
    // Identity (read-only)
    // =====================================================================

    /** Shows exactly what the chaincode reads from the caller's X.509 certificate. */
    async WhoAmI(ctx) {
        const c = this._caller(ctx);
        return JSON.stringify({ ...c, x509Id: ctx.clientIdentity.getID() });
    }

    // =====================================================================
    // Org1 — Registration
    // =====================================================================

    /** Registrar enters an existing parcel into the digital registry. */
    async RegisterParcel(ctx, surveyNo, village, district, areaSqm, ownerName, ownerRef) {
        const c = this._require(ctx, 'RegisterParcel', REGISTRATION, ['registrar']);
        surveyNo = this._requireText(surveyNo, 'surveyNo');

        if (await this._get(ctx, 'parcel', surveyNo)) {
            throw new Error(`Parcel ${surveyNo} is already registered`);
        }

        const now = this._now(ctx);
        const parcel = {
            docType: 'parcel',
            surveyNo,
            village: this._requireText(village, 'village'),
            district: this._requireText(district, 'district'),
            areaSqm: this._requireNumber(areaSqm, 'areaSqm', { allowZero: false }),
            ownerName: this._requireText(ownerName, 'ownerName'),
            ownerRef: this._requireText(ownerRef, 'ownerRef'),
            status: 'ACTIVE',
            pendingApplication: null,
            lastTransfer: null,
            registeredBy: c.name,
            registeredAt: now,
            updatedAt: now,
        };
        await this._put(ctx, 'parcel', surveyNo, parcel);
        this._emit(ctx, 'ParcelRegistered', { surveyNo, ownerName: parcel.ownerName });
        return JSON.stringify(parcel);
    }

    /** Clerk opens a transfer file. Locks the parcel against a second sale. */
    async SubmitTransferApplication(ctx, appId, surveyNo, sellerRef, buyerName, buyerRef, considerationINR) {
        const c = this._require(ctx, 'SubmitTransferApplication', REGISTRATION, ['clerk']);
        appId = this._requireText(appId, 'appId');
        sellerRef = this._requireText(sellerRef, 'sellerRef');
        buyerRef = this._requireText(buyerRef, 'buyerRef');

        if (await this._get(ctx, 'application', appId)) {
            throw new Error(`Application ${appId} already exists`);
        }
        const parcel = await this._mustGet(ctx, 'parcel', surveyNo, 'Parcel');

        if (parcel.status !== 'ACTIVE') {
            throw new Error(
                `Parcel ${surveyNo} is locked by pending application ${parcel.pendingApplication} ` +
                '-- a second transfer cannot be opened until that one is decided');
        }
        if (parcel.ownerRef !== sellerRef) {
            throw new Error(`Seller is not the registered owner of parcel ${surveyNo}`);
        }
        if (buyerRef === sellerRef) {
            throw new Error('Buyer and seller cannot be the same person');
        }

        const now = this._now(ctx);
        const app = {
            docType: 'application',
            appId,
            kind: 'TRANSFER',
            surveyNo,
            sellerName: parcel.ownerName,
            sellerRef,
            buyerName: this._requireText(buyerName, 'buyerName'),
            buyerRef,
            considerationINR: this._requireNumber(considerationINR, 'considerationINR'),
            status: STATUS.SUBMITTED,
            documents: [],
            verification: null,
            survey: null,
            decision: null,
            createdBy: c.name,
            createdAt: now,
            updatedAt: now,
            trail: [],
        };
        this._trail(ctx, app, c, 'SUBMITTED');

        parcel.status = 'TRANSFER_PENDING';
        parcel.pendingApplication = appId;
        parcel.updatedAt = now;

        await this._put(ctx, 'application', appId, app);
        await this._put(ctx, 'parcel', surveyNo, parcel);
        this._emit(ctx, 'ApplicationSubmitted', { appId, surveyNo });
        return JSON.stringify(app);
    }

    /**
     * Clerk anchors the SHA-256 fingerprint of a DigiLocker document to the file.
     * A fingerprint can be anchored only once, so the same deed cannot be reused
     * to back two different applications.
     */
    async AnchorDocument(ctx, appId, docSha256, docId, documentType, issuer) {
        const c = this._require(ctx, 'AnchorDocument', REGISTRATION, ['clerk']);
        const app = await this._mustGet(ctx, 'application', appId, 'Application');
        this._mustStatus(app, [STATUS.SUBMITTED]);

        const sha = this._requireText(docSha256, 'docSha256').toLowerCase();
        if (!SHA256_RE.test(sha)) {
            throw new Error('docSha256 must be a 64-character hex SHA-256 digest');
        }
        const existing = await this._get(ctx, 'docanchor', sha);
        if (existing) {
            throw new Error(`Document ${sha.slice(0, 12)}... is already anchored to application ${existing.appId}`);
        }

        const anchor = {
            docType: 'docanchor',
            sha256: sha,
            appId,
            docId: this._requireText(docId, 'docId'),
            documentType: this._requireText(documentType, 'documentType'),
            issuer: this._requireText(issuer, 'issuer'),
            anchoredBy: c.name,
            anchoredAt: this._now(ctx),
            txId: ctx.stub.getTxID(),
        };
        app.documents.push({
            sha256: sha, docId: anchor.docId, documentType: anchor.documentType, issuer: anchor.issuer,
        });
        this._trail(ctx, app, c, `ANCHORED ${anchor.documentType} ${anchor.docId}`);

        await this._put(ctx, 'docanchor', sha, anchor);
        await this._put(ctx, 'application', appId, app);
        this._emit(ctx, 'DocumentAnchored', { appId, sha256: sha });
        return JSON.stringify(anchor);
    }

    /** Registrar hands the file to Revenue. This is the Org1 -> Org2 handoff. */
    async ForwardToRevenue(ctx, appId) {
        const c = this._require(ctx, 'ForwardToRevenue', REGISTRATION, ['registrar']);
        const app = await this._mustGet(ctx, 'application', appId, 'Application');
        this._mustStatus(app, [STATUS.SUBMITTED]);
        if (app.documents.length === 0) {
            throw new Error(`Application ${appId} has no anchored documents; anchor at least one before forwarding`);
        }
        app.status = STATUS.WITH_REVENUE;
        this._trail(ctx, app, c, 'FORWARDED to Revenue');
        await this._put(ctx, 'application', appId, app);
        this._emit(ctx, 'HandoffToRevenue', { appId, surveyNo: app.surveyNo, documents: app.documents.length });
        return JSON.stringify(app);
    }

    // =====================================================================
    // Org2 — Revenue
    // =====================================================================

    /**
     * Verifier independently re-fetches every document from DigiLocker, hashes it,
     * and submits the fingerprints. The contract compares them with the anchors
     * written by Org1. Any difference is recorded on the ledger as FLAGGED.
     */
    async VerifyDocuments(ctx, appId, recomputedHashesJson) {
        const c = this._require(ctx, 'VerifyDocuments', REVENUE, ['verifier']);
        const app = await this._mustGet(ctx, 'application', appId, 'Application');
        this._mustStatus(app, [STATUS.WITH_REVENUE]);

        let submitted;
        try {
            submitted = JSON.parse(recomputedHashesJson);
        } catch (e) {
            throw new Error('recomputedHashesJson must be a JSON array of SHA-256 hex strings');
        }
        if (!Array.isArray(submitted)) {
            throw new Error('recomputedHashesJson must be a JSON array of SHA-256 hex strings');
        }
        const got = new Set(submitted.map((h) => String(h).toLowerCase()));
        const anchored = new Set(app.documents.map((d) => d.sha256));

        const missing = app.documents.filter((d) => !got.has(d.sha256)).map((d) => d.docId);
        const unexpected = [...got].filter((h) => !anchored.has(h)).map((h) => `${h.slice(0, 12)}...`);
        const ok = missing.length === 0 && unexpected.length === 0;

        app.verification = {
            verifiedBy: c.name,
            at: this._now(ctx),
            result: ok ? 'MATCH' : 'MISMATCH',
            documentsChecked: app.documents.length,
            mismatchedDocuments: missing,
            unexpectedFingerprints: unexpected,
        };
        app.status = ok ? STATUS.DOCS_VERIFIED : STATUS.FLAGGED;
        this._trail(ctx, app, c, ok ? 'DOCUMENTS VERIFIED' : 'FLAGGED: fingerprint mismatch');
        await this._put(ctx, 'application', appId, app);
        this._emit(ctx, ok ? 'DocumentsVerified' : 'ApplicationFlagged', { appId, result: app.verification.result });
        return JSON.stringify(app);
    }

    /** Surveyor records the field measurement; deviation beyond tolerance is flagged for the Collector. */
    async RecordSurvey(ctx, appId, measuredAreaSqm, boundaries) {
        const c = this._require(ctx, 'RecordSurvey', REVENUE, ['surveyor']);
        const app = await this._mustGet(ctx, 'application', appId, 'Application');
        this._mustStatus(app, [STATUS.DOCS_VERIFIED]);
        const parcel = await this._mustGet(ctx, 'parcel', app.surveyNo, 'Parcel');

        const measured = this._requireNumber(measuredAreaSqm, 'measuredAreaSqm', { allowZero: false });
        const deviation = Math.round((Math.abs(measured - parcel.areaSqm) / parcel.areaSqm) * 10000) / 100;

        app.survey = {
            surveyedBy: c.name,
            at: this._now(ctx),
            recordedAreaSqm: parcel.areaSqm,
            measuredAreaSqm: measured,
            deviationPct: deviation,
            tolerancePct: SURVEY_TOLERANCE_PCT,
            withinTolerance: deviation <= SURVEY_TOLERANCE_PCT,
            boundaries: this._requireText(boundaries, 'boundaries'),
        };
        app.status = STATUS.SURVEYED;
        this._trail(ctx, app, c, `SURVEYED (deviation ${deviation}%)`);
        await this._put(ctx, 'application', appId, app);
        this._emit(ctx, 'SurveyRecorded', { appId, deviationPct: deviation });
        return JSON.stringify(app);
    }

    /** Verifier hands the completed file to the Collectorate. This is the Org2 -> Org3 handoff. */
    async ForwardToCollectorate(ctx, appId) {
        const c = this._require(ctx, 'ForwardToCollectorate', REVENUE, ['verifier']);
        const app = await this._mustGet(ctx, 'application', appId, 'Application');
        this._mustStatus(app, [STATUS.SURVEYED]);
        app.status = STATUS.WITH_COLLECTORATE;
        this._trail(ctx, app, c, 'FORWARDED to Collectorate');
        await this._put(ctx, 'application', appId, app);
        this._emit(ctx, 'HandoffToCollectorate', { appId });
        return JSON.stringify(app);
    }

    // =====================================================================
    // Org3 — Collectorate (the only place ownership can change)
    // =====================================================================

    async ApproveTransfer(ctx, appId, remarks) {
        const c = this._require(ctx, 'ApproveTransfer', COLLECTORATE, ['collector']);
        const app = await this._mustGet(ctx, 'application', appId, 'Application');
        this._mustStatus(app, [STATUS.WITH_COLLECTORATE]);
        const parcel = await this._mustGet(ctx, 'parcel', app.surveyNo, 'Parcel');

        if (parcel.pendingApplication !== appId || parcel.ownerRef !== app.sellerRef) {
            throw new Error(`Parcel ${app.surveyNo} no longer matches application ${appId}; refusing to transfer`);
        }

        const now = this._now(ctx);
        app.status = STATUS.APPROVED;
        app.decision = { by: c.name, at: now, outcome: 'APPROVED', remarks: remarks || '' };
        this._trail(ctx, app, c, 'APPROVED -- ownership transferred');

        parcel.lastTransfer = {
            appId, fromName: parcel.ownerName, toName: app.buyerName, at: now, txId: ctx.stub.getTxID(),
        };
        parcel.ownerName = app.buyerName;
        parcel.ownerRef = app.buyerRef;
        parcel.status = 'ACTIVE';
        parcel.pendingApplication = null;
        parcel.updatedAt = now;

        await this._put(ctx, 'application', appId, app);
        await this._put(ctx, 'parcel', app.surveyNo, parcel);
        this._emit(ctx, 'TransferApproved', { appId, surveyNo: app.surveyNo, newOwner: app.buyerName });
        return JSON.stringify(app);
    }

    async RejectApplication(ctx, appId, reason) {
        const c = this._require(ctx, 'RejectApplication', COLLECTORATE, ['collector']);
        const app = await this._mustGet(ctx, 'application', appId, 'Application');
        if (app.status === STATUS.APPROVED || app.status === STATUS.REJECTED) {
            throw new Error(`Application ${appId} is already ${app.status}`);
        }
        const now = this._now(ctx);
        app.status = STATUS.REJECTED;
        app.decision = { by: c.name, at: now, outcome: 'REJECTED', remarks: this._requireText(reason, 'reason') };
        this._trail(ctx, app, c, 'REJECTED');

        const parcel = await this._mustGet(ctx, 'parcel', app.surveyNo, 'Parcel');
        if (parcel.pendingApplication === appId) {
            parcel.status = 'ACTIVE';
            parcel.pendingApplication = null;
            parcel.updatedAt = now;
            await this._put(ctx, 'parcel', app.surveyNo, parcel);
        }
        await this._put(ctx, 'application', appId, app);
        this._emit(ctx, 'ApplicationRejected', { appId });
        return JSON.stringify(app);
    }

    // =====================================================================
    // Queries — open to every channel member (transparency)
    // =====================================================================

    async ReadParcel(ctx, surveyNo) {
        return JSON.stringify(await this._mustGet(ctx, 'parcel', surveyNo, 'Parcel'));
    }

    async ReadApplication(ctx, appId) {
        return JSON.stringify(await this._mustGet(ctx, 'application', appId, 'Application'));
    }

    /** Public check: has this exact document been anchored, and to what? */
    async VerifyDocument(ctx, docSha256) {
        const sha = String(docSha256 || '').toLowerCase();
        const anchor = await this._get(ctx, 'docanchor', sha);
        return JSON.stringify(anchor ? { anchored: true, ...anchor } : { anchored: false, sha256: sha });
    }

    /** Every committed version of a parcel: the chain of ownership. */
    async ParcelHistory(ctx, surveyNo) {
        await this._mustGet(ctx, 'parcel', surveyNo, 'Parcel');
        return JSON.stringify(await this._history(ctx, 'parcel', surveyNo));
    }

    async ApplicationHistory(ctx, appId) {
        await this._mustGet(ctx, 'application', appId, 'Application');
        return JSON.stringify(await this._history(ctx, 'application', appId));
    }

    /** status = '' or 'ALL' returns everything. */
    async ListApplications(ctx, status) {
        const all = await this._scan(ctx, 'application');
        const want = String(status || '').toUpperCase();
        return JSON.stringify(want && want !== 'ALL' ? all.filter((a) => a.status === want) : all);
    }

    async ListParcels(ctx) {
        return JSON.stringify(await this._scan(ctx, 'parcel'));
    }
}

module.exports = LandRegistry;
