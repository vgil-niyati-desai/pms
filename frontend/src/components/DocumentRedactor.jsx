import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import FilePreview from "./FilePreview";
import {
  createRedactedCopy,
  getRedactionSource,
  getSensitiveSuggestions,
  redactionPageImageUrl,
} from "../api/documents";

/**
 * Redaction: cover the sensitive parts of a document, check the result, then
 * download a permanently redacted copy of it.
 *
 * Boxes reach the page two ways, and they are the same kind of thing once
 * they get there.
 *
 *   * **Drawn by hand.** Drag across the page, exactly as before.
 *   * **Suggested.** On open, the backend scans the document for financial
 *     figures — prices, contract values, rates, EMD, totals — and returns
 *     rectangles in the same normalised format. It reads a normal PDF's text
 *     layer, and runs OCR over scanned pages, JPGs and PNGs; a mixed PDF gets
 *     whichever treatment each page needs. They arrive as *proposals*:
 *     nothing is covered until a person accepts it, and an accepted one
 *     simply joins the list posted to the ordinary redaction endpoint.
 *     Rejecting one drops it and nothing else happens.
 *
 * A suggestion read by OCR is marked as such. OCR is a guess about pixels in
 * a way reading a content stream is not, so those get a badge and the panel
 * says which pages they came from — the reviewer is the one who can check the
 * box against the page, and can only do it if told where to look.
 *
 * The two are kept visibly apart on purpose. A confirmed redaction — drawn or
 * accepted — is solid white, because that is what the generated copy will
 * look like. A suggestion still waiting on a decision is a dashed amber
 * outline with the page showing through it, so it can never be mistaken for
 * something already covered. The count in the bar only ever counts confirmed
 * areas, for the same reason.
 *
 * Three things about the mechanics are worth knowing.
 *
 * The page is an image, not the usual preview. The normal preview hands a PDF
 * to the browser's own viewer inside an <iframe>, and an iframe is opaque —
 * nothing outside it can tell which page a click landed on, or where. So
 * redaction asks the backend to render each page and draws over that instead,
 * which also means PDFs and PNG/JPGs are selected in exactly the same way.
 *
 * The boxes here are only a *selection*. They are in fractions of the page
 * (origin top-left), sent to the backend, and it is the backend that deletes
 * the covered text from the copy it generates. Nothing on this screen is what
 * makes the copy safe, and nothing on this screen changes the original — it
 * is still there, unredacted, behind Close. Scanning for suggestions does not
 * change it either; that endpoint only reads.
 *
 * Preview redaction is therefore not a mock-up of the result: it asks the
 * backend for the real copy and shows that. The bytes it displays are cached
 * and are the same bytes Generate then saves, so what was checked is exactly
 * what lands on disk. In the preview a PDF opens in the browser's own viewer,
 * which means the redaction can be tested the way a recipient would test it —
 * by trying to select the text that used to be there and finding nothing.
 */

/**
 * What the page image is rendered at, in two coarse tiers.
 *
 * Tiers rather than a width per zoom level, because the width is part of the
 * image's URL: a value that moved on every step would re-fetch the page on
 * every click of +. The lower tier is already about twice the size the page
 * is displayed at, which is sharp on a high-density screen; the upper one is
 * the most the backend will render (MAX_PAGE_IMAGE_WIDTH) and is what keeps
 * small figures legible once they are being zoomed into.
 */
function pageRenderWidth(zoom) {
  return zoom >= 2 ? 2600 : 1600;
}

/** Below this, a drag is a stray click rather than a selection. */
const MIN_AREA = 0.004;

/**
 * The zoom levels the − and + buttons step through.
 *
 * 1 is the page fitted to the stage. Zoom is applied by scaling the drawing
 * surface itself, so it changes nothing about how an area is recorded: the
 * rectangles are fractions of that surface's measured box either way, and a
 * box drawn at 400% describes the same part of the page as the same box drawn
 * at 100%. What zoom buys is precision — at 400% a figure a few millimetres
 * tall is a few centimetres of screen to aim at.
 */
const ZOOM_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3, 4];
const DEFAULT_ZOOM = 1;

/** Where a suggestion stands. Only `accepted` ones are ever redacted. */
const PENDING = "pending";
const ACCEPTED = "accepted";
const REJECTED = "rejected";

function clamp01(value) {
  return Math.min(1, Math.max(0, value));
}

export default function DocumentRedactor({ documentId, fileName, onCancel }) {
  const [source, setSource] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [pageIndex, setPageIndex] = useState(0);
  const [pageLoaded, setPageLoaded] = useState(false);

  const [areas, setAreas] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [draft, setDraft] = useState(null);
  const [zoom, setZoom] = useState(DEFAULT_ZOOM);
  const [panning, setPanning] = useState(false);

  // The automatic pass: what it found, what it could not read, and whether it
  // is still running. Its failure is deliberately not fatal — the manual tool
  // has to keep working when the scan cannot.
  const [suggestions, setSuggestions] = useState([]);
  const [scan, setScan] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [scanError, setScanError] = useState("");
  const [reviewOpen, setReviewOpen] = useState(true);

  // "preview" or "download" while that action is in flight, "" otherwise.
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const [preview, setPreview] = useState(null);

  const surfaceRef = useRef(null);
  const stageRef = useRef(null);
  const dragOriginRef = useRef(null);
  const nextIdRef = useRef(1);
  // Where a middle-button pan started: the pointer position and the stage's
  // scroll offset at that moment.
  const panOriginRef = useRef(null);
  // The point the view was centred on when zoom last changed, as a fraction
  // of the scrollable area, so the same part of the page stays in view.
  const zoomAnchorRef = useRef(null);
  // The last copy the backend built, kept so previewing and then downloading
  // does not generate it twice — and so the file that downloads is byte-for-
  // byte the one that was previewed.
  const builtRef = useRef(null);
  // Object URLs have to be revoked by hand, so the live one is tracked in a
  // ref as well as in state; state alone cannot be read during cleanup.
  const previewRef = useRef(null);

  // How many pages there are, and how tall each one is. Until this arrives
  // there is nothing to draw on.
  useEffect(() => {
    let cancelled = false;
    setSource(null);
    setLoadError("");
    getRedactionSource(documentId)
      .then((data) => {
        if (!cancelled) setSource(data);
      })
      .catch((err) => {
        if (!cancelled) setLoadError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [documentId]);

  /**
   * Scan for financial figures, once, as the document opens.
   *
   * Separate from loading the source so a scan that fails — or a backend
   * without the detection endpoint — costs the suggestions and nothing else.
   * The page still renders and boxes can still be drawn by hand, which is why
   * this sets `scanError` rather than `loadError`.
   */
  useEffect(() => {
    let cancelled = false;
    setSuggestions([]);
    setScan(null);
    setScanError("");
    setScanning(true);
    getSensitiveSuggestions(documentId)
      .then((data) => {
        if (cancelled) return;
        setScan(data);
        setSuggestions(
          (data.detections || []).map((detection) => ({
            ...detection,
            status: PENDING,
          })),
        );
      })
      .catch((err) => {
        if (!cancelled) setScanError(err.message);
      })
      .finally(() => {
        if (!cancelled) setScanning(false);
      });
    return () => {
      cancelled = true;
    };
  }, [documentId]);

  const pages = source?.pages ?? [];
  const page = pages[pageIndex];

  /**
   * Every box that will actually be redacted: drawn by hand, plus the
   * suggestions a person accepted.
   *
   * One list from here on. The backend has no idea which is which, and there
   * is nothing for it to know — an accepted suggestion is a rectangle on a
   * page, the same as a drawn one.
   */
  const confirmed = useMemo(
    () => [
      ...areas,
      ...suggestions.filter((suggestion) => suggestion.status === ACCEPTED),
    ],
    [areas, suggestions],
  );

  const pending = useMemo(
    () => suggestions.filter((suggestion) => suggestion.status === PENDING),
    [suggestions],
  );
  const pendingHere = pending.filter((suggestion) => suggestion.page === pageIndex);
  const pendingElsewhere = pending.length - pendingHere.length;
  const acceptedCount = suggestions.filter(
    (suggestion) => suggestion.status === ACCEPTED,
  ).length;
  const confirmedHere = confirmed.filter((area) => area.page === pageIndex);

  // A new page is a new image to wait for.
  useEffect(() => {
    setPageLoaded(false);
  }, [pageIndex]);

  /**
   * Step to another zoom level, keeping the middle of the view where it is.
   *
   * Without the anchor, zooming in from a spot halfway down a page throws the
   * view back towards the top-left and the thing being aimed at has to be
   * found again — which defeats the point of zooming to place a box on it.
   */
  function changeZoom(next) {
    const stage = stageRef.current;
    if (stage && stage.scrollWidth > 0 && stage.scrollHeight > 0) {
      zoomAnchorRef.current = {
        x: (stage.scrollLeft + stage.clientWidth / 2) / stage.scrollWidth,
        y: (stage.scrollTop + stage.clientHeight / 2) / stage.scrollHeight,
      };
    }
    setZoom(next);
  }

  // Put the anchored point back in the middle. Before paint, so the view does
  // not visibly jump to the new scroll position after landing at the old one.
  useLayoutEffect(() => {
    const stage = stageRef.current;
    const anchor = zoomAnchorRef.current;
    zoomAnchorRef.current = null;
    if (!stage || !anchor) return;
    stage.scrollLeft = anchor.x * stage.scrollWidth - stage.clientWidth / 2;
    stage.scrollTop = anchor.y * stage.scrollHeight - stage.clientHeight / 2;
  }, [zoom]);

  const zoomStep = ZOOM_STEPS.indexOf(zoom);
  const canZoomOut = zoomStep > 0;
  const canZoomIn = zoomStep < ZOOM_STEPS.length - 1;

  const showPreview = useCallback((next) => {
    if (previewRef.current) URL.revokeObjectURL(previewRef.current.url);
    previewRef.current = next;
    setPreview(next);
  }, []);

  // Changing what would be redacted makes any built copy stale: it no longer
  // shows what would download. Dropping both here means the two can never
  // disagree, whichever action changed the set — a drawn box, a removed one,
  // or a suggestion being accepted or rejected.
  useEffect(() => {
    builtRef.current = null;
    showPreview(null);
  }, [confirmed, showPreview]);

  // Nothing else revokes the last object URL when the redactor unmounts.
  // Done through the ref rather than showPreview, which would also be setting
  // state on a component that is going away.
  useEffect(
    () => () => {
      if (previewRef.current) URL.revokeObjectURL(previewRef.current.url);
    },
    [],
  );

  const setSuggestionStatus = useCallback((id, status) => {
    setSuggestions((current) =>
      current.map((suggestion) =>
        suggestion.id === id ? { ...suggestion, status } : suggestion,
      ),
    );
    setDone("");
  }, []);

  /** Accept or reject every suggestion still waiting, across all pages. */
  function decideAll(status) {
    setSuggestions((current) =>
      current.map((suggestion) =>
        suggestion.status === PENDING ? { ...suggestion, status } : suggestion,
      ),
    );
    setSelectedId(null);
    setDone("");
  }

  /**
   * Take the highlighted box off the page.
   *
   * What that means depends on where the box came from, and both readings are
   * the same gesture: a drawn area is deleted, a suggestion is rejected. An
   * accepted suggestion goes back to pending rather than vanishing, so a
   * mis-click is one click to undo instead of a lost detection.
   */
  const removeSelected = useCallback(() => {
    if (selectedId === null) return;
    const suggestion = suggestions.find((entry) => entry.id === selectedId);
    if (suggestion) {
      setSuggestionStatus(
        selectedId,
        suggestion.status === ACCEPTED ? PENDING : REJECTED,
      );
      setSelectedId(null);
      return;
    }
    setAreas((current) => current.filter((area) => area.id !== selectedId));
    setSelectedId(null);
    setDone("");
  }, [selectedId, suggestions, setSuggestionStatus]);

  // Delete and Backspace remove the highlighted box, which is what every
  // other canvas-shaped tool does. Skipped while typing in a field.
  useEffect(() => {
    function onKeyDown(e) {
      if (e.key !== "Delete" && e.key !== "Backspace") return;
      const tag = e.target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || e.target?.isContentEditable) return;
      if (selectedId === null) return;
      e.preventDefault();
      removeSelected();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [selectedId, removeSelected]);

  function pointFrom(event) {
    const bounds = surfaceRef.current.getBoundingClientRect();
    return {
      x: clamp01((event.clientX - bounds.left) / bounds.width),
      y: clamp01((event.clientY - bounds.top) / bounds.height),
    };
  }

  function rectBetween(a, b) {
    return {
      x: Math.min(a.x, b.x),
      y: Math.min(a.y, b.y),
      width: Math.abs(a.x - b.x),
      height: Math.abs(a.y - b.y),
    };
  }

  function startDraw(event) {
    // The middle button drags the page around instead of drawing on it, which
    // is the quick way to reach the rest of a zoomed page without going to
    // the scrollbars. The stage scrolls normally too.
    if (event.button === 1 && stageRef.current) {
      event.preventDefault();
      event.currentTarget.setPointerCapture(event.pointerId);
      panOriginRef.current = {
        x: event.clientX,
        y: event.clientY,
        left: stageRef.current.scrollLeft,
        top: stageRef.current.scrollTop,
      };
      setPanning(true);
      return;
    }
    // Left button only for drawing: a right-click is the context menu.
    if (event.button !== 0 || !pageLoaded) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    const origin = pointFrom(event);
    dragOriginRef.current = origin;
    setSelectedId(null);
    setDone("");
    setDraft({ x: origin.x, y: origin.y, width: 0, height: 0 });
  }

  function moveDraw(event) {
    const pan = panOriginRef.current;
    if (pan) {
      const stage = stageRef.current;
      // The page follows the pointer, so the scroll offset moves against it.
      stage.scrollLeft = pan.left - (event.clientX - pan.x);
      stage.scrollTop = pan.top - (event.clientY - pan.y);
      return;
    }
    if (!dragOriginRef.current) return;
    setDraft(rectBetween(dragOriginRef.current, pointFrom(event)));
  }

  function endDraw(event) {
    if (panOriginRef.current) {
      panOriginRef.current = null;
      setPanning(false);
      return;
    }
    if (!dragOriginRef.current) return;
    const rect = rectBetween(dragOriginRef.current, pointFrom(event));
    dragOriginRef.current = null;
    setDraft(null);
    // A click that never moved is a click, not an area worth redacting.
    if (rect.width < MIN_AREA || rect.height < MIN_AREA) return;
    // Prefixed, because suggestions carry ids of their own and the two sets
    // share one `selectedId`.
    const id = `manual-${nextIdRef.current++}`;
    setAreas((current) => [...current, { id, page: pageIndex, ...rect }]);
    setSelectedId(id);
  }

  function cancelDraw() {
    dragOriginRef.current = null;
    panOriginRef.current = null;
    setPanning(false);
    setDraft(null);
  }

  /** Clear the drawn boxes. Suggestions are decided, not cleared. */
  function clearAll() {
    setAreas([]);
    setSelectedId(null);
    setDone("");
  }

  /** The next page that still has something waiting to be reviewed. */
  function goToPending() {
    const next =
      pending.find((suggestion) => suggestion.page > pageIndex) ?? pending[0];
    if (next) {
      setPageIndex(next.page);
      setSelectedId(next.id);
    }
  }

  /**
   * The redacted copy for the areas as they stand, built once and reused.
   *
   * Previewing and then downloading must not produce two different files, and
   * regenerating a large PDF to hand over bytes we already hold is wasted work
   * on both sides. The cache is dropped the moment the selection changes.
   */
  async function buildCopy() {
    if (builtRef.current) return builtRef.current;
    const payload = confirmed.map(({ page: index, x, y, width, height }) => ({
      page: index,
      x,
      y,
      width,
      height,
    }));
    const { blob, fileName: downloadName } = await createRedactedCopy(documentId, payload);
    builtRef.current = { blob, fileName: downloadName };
    return builtRef.current;
  }

  async function previewRedaction() {
    setBusy("preview");
    setError("");
    setDone("");
    try {
      const built = await buildCopy();
      // The real generated file, shown through the same viewer the rest of
      // the app uses. An object URL because the copy is never stored on the
      // server, so there is no address to point the viewer at.
      showPreview({ url: URL.createObjectURL(built.blob), fileName: built.fileName });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy("");
    }
  }

  async function generate() {
    setBusy("download");
    setError("");
    setDone("");
    try {
      const built = await buildCopy();
      // Saved straight from the bytes in hand: the copy only ever exists in
      // this download, so there is no URL on the server to link to instead.
      const url = URL.createObjectURL(built.blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = built.fileName;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      setDone(`Downloaded ${built.fileName}. The original is unchanged.`);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy("");
    }
  }

  if (loadError) {
    return (
      <div className="redact-message">
        <div className="alert alert-error">{loadError}</div>
        <button type="button" className="btn" onClick={onCancel}>
          Back to preview
        </button>
      </div>
    );
  }

  if (!source) {
    return <p className="muted">Opening {fileName || "the document"} for redaction…</p>;
  }

  const pageCount = pages.length;
  const aspect = page && page.width ? page.height / page.width : 1.414;
  const selectedSuggestion = suggestions.find((entry) => entry.id === selectedId);

  return (
    <div className="redact">
      <div className="redact-bar">
        <div className="redact-bar-info">
          {preview ? (
            <span className="muted">
              Previewing the redacted copy — <strong>{confirmed.length}</strong>
              {confirmed.length === 1 ? " area applied" : " areas applied"}
            </span>
          ) : (
            <>
              <strong>{confirmed.length}</strong>
              <span className="muted">
                {confirmed.length === 1 ? " area to redact" : " areas to redact"}
                {pageCount > 1 && confirmed.length > 0 && ` (${confirmedHere.length} on this page)`}
                {acceptedCount > 0 && `, ${acceptedCount} accepted from the scan`}
              </span>
            </>
          )}
        </div>
        <div className="redact-bar-actions">
          {preview ? (
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => showPreview(null)}
              disabled={busy !== ""}
            >
              Back to selection
            </button>
          ) : (
            <>
              <button
                type="button"
                className="btn btn-sm"
                onClick={removeSelected}
                disabled={selectedId === null || busy !== ""}
              >
                {selectedSuggestion
                  ? selectedSuggestion.status === ACCEPTED
                    ? "Undo accept"
                    : "Reject suggestion"
                  : "Remove selection"}
              </button>
              <button
                type="button"
                className="btn btn-sm"
                onClick={clearAll}
                disabled={areas.length === 0 || busy !== ""}
              >
                Clear drawn boxes
              </button>
              <button
                type="button"
                className="btn btn-sm"
                onClick={previewRedaction}
                disabled={confirmed.length === 0 || busy !== ""}
              >
                {busy === "preview" ? "Preparing…" : "Preview redaction"}
              </button>
            </>
          )}
          <button
            type="button"
            className="btn btn-sm btn-primary"
            onClick={generate}
            disabled={confirmed.length === 0 || busy !== ""}
          >
            {busy === "download" ? "Generating…" : "Generate redacted copy"}
          </button>
          <button type="button" className="btn btn-sm" onClick={onCancel} disabled={busy !== ""}>
            Cancel
          </button>
        </div>
      </div>

      {error && <div className="alert alert-error">{error}</div>}
      {done && <div className="alert alert-success">{done}</div>}

      <p className="redact-hint muted">
        {preview ? (
          <>
            This is the copy that will download — the covered text has been removed from it,
            not hidden, so there is nothing left to select underneath. Back to selection to
            adjust the areas; the original entry is not changed either way.
          </>
        ) : (
          <>
            Dashed amber boxes are suggestions from the scan and cover nothing until you
            accept them; a dotted one was read by OCR off a scanned page, so it is worth
            checking against the page. Solid white boxes are what will be redacted. Drag
            across the page to add one by hand, click any box to see what is under it, then
            press Delete. Preview redaction shows the finished copy before you download it —
            the original entry is not changed.
          </>
        )}
      </p>

      {preview ? (
        // The generated file itself, in the same viewer the rest of the app
        // uses — a PDF opens in the browser's own reader, so the redaction can
        // be tested by trying to select the text that used to be there.
        <div className="redact-preview">
          <FilePreview url={preview.url} fileName={preview.fileName} fill />
        </div>
      ) : (
        // Stage and review side by side, and this row is the only thing that
        // grows. Everything else in the column is fixed height, so the page
        // keeps its area no matter how many suggestions there are — which is
        // what went wrong when the review panel lived in the column and
        // squeezed the document down to a sliver.
        <div className="redact-main">
          <div className="redact-stage" ref={stageRef}>
          <div
            className={panning ? "redact-surface redact-surface-panning" : "redact-surface"}
            ref={surfaceRef}
            // The zoom is a plain multiplier on the surface's width; the
            // rectangles are fractions of whatever that comes out as, so
            // nothing about the selection has to know it is in play.
            style={{ aspectRatio: `1 / ${aspect}`, "--redact-zoom": zoom }}
            onPointerDown={startDraw}
            onPointerMove={moveDraw}
            onPointerUp={endDraw}
            onPointerCancel={cancelDraw}
            // Windows Chrome opens its autoscroll widget on a middle-button
            // mousedown, which fights the pan. preventDefault here stops it.
            onMouseDown={(e) => {
              if (e.button === 1) e.preventDefault();
            }}
          >
            <img
              key={pageIndex}
              className="redact-page"
              src={redactionPageImageUrl(documentId, pageIndex, pageRenderWidth(zoom))}
              alt={`Page ${pageIndex + 1} of ${fileName || "the document"}`}
              draggable={false}
              onLoad={() => setPageLoaded(true)}
              onError={() => setLoadError("This page could not be rendered.")}
            />
            {!pageLoaded && <p className="redact-loading muted">Rendering page…</p>}

            {/* Suggestions still waiting on a decision. Under the confirmed
                boxes in the DOM, so an accepted one drawn over the same spot
                wins — and translucent either way, so the figure stays
                readable while it is being judged. */}
            {pendingHere.map((suggestion) => (
              <button
                type="button"
                key={suggestion.id}
                className={[
                  "redact-area redact-area-suggested",
                  suggestion.source === "ocr" ? "redact-area-suggested-ocr" : "",
                  suggestion.id === selectedId ? "redact-area-suggested-selected" : "",
                ]
                  .filter(Boolean)
                  .join(" ")}
                style={{
                  left: `${suggestion.x * 100}%`,
                  top: `${suggestion.y * 100}%`,
                  width: `${suggestion.width * 100}%`,
                  height: `${suggestion.height * 100}%`,
                }}
                title={`${suggestion.label}: ${suggestion.text}${
                  suggestion.source === "ocr" ? " (read by OCR)" : ""
                }`}
                aria-label={`Suggested redaction on page ${pageIndex + 1}: ${suggestion.label}, ${suggestion.text}${
                  suggestion.source === "ocr" ? ", read by OCR" : ""
                }. Select to accept or reject.`}
                onPointerDown={(e) => {
                  e.stopPropagation();
                  setSelectedId(suggestion.id);
                  setDone("");
                }}
                // Double-click accepts, for working down a page of figures
                // without going back to the list for each one.
                onDoubleClick={() => setSuggestionStatus(suggestion.id, ACCEPTED)}
              />
            ))}

            {confirmedHere.map((area) => (
              <button
                type="button"
                key={area.id}
                className={
                  area.id === selectedId ? "redact-area redact-area-selected" : "redact-area"
                }
                style={{
                  left: `${area.x * 100}%`,
                  top: `${area.y * 100}%`,
                  width: `${area.width * 100}%`,
                  height: `${area.height * 100}%`,
                }}
                aria-label={`Redaction area on page ${pageIndex + 1}. Select to remove.`}
                // Selecting a box must not also start drawing a new one on top
                // of it, which is what the surface's own handler would do.
                onPointerDown={(e) => {
                  e.stopPropagation();
                  setSelectedId(area.id);
                  setDone("");
                }}
              />
            ))}

            {draft && (
              <div
                className="redact-area redact-area-draft"
                style={{
                  left: `${draft.x * 100}%`,
                  top: `${draft.y * 100}%`,
                  width: `${draft.width * 100}%`,
                  height: `${draft.height * 100}%`,
                }}
              />
            )}
          </div>
        </div>

        {/* The review panel, beside the page rather than above it. It scrolls
            inside itself, so a hundred suggestions cost the document no
            height at all. */}
        <SuggestionReview
          scanning={scanning}
          scanError={scanError}
          scan={scan}
          pending={pending}
          pendingHere={pendingHere}
          pendingElsewhere={pendingElsewhere}
          open={reviewOpen}
          onToggle={() => setReviewOpen((value) => !value)}
          selectedId={selectedId}
          onSelect={setSelectedId}
          onAccept={(id) => setSuggestionStatus(id, ACCEPTED)}
          onReject={(id) => setSuggestionStatus(id, REJECTED)}
          onAcceptAll={() => decideAll(ACCEPTED)}
          onRejectAll={() => decideAll(REJECTED)}
          onGoToPending={goToPending}
          pageCount={pageCount}
          busy={busy !== ""}
        />
        </div>
      )}

      {!preview && (
        <div className="redact-footer">
          <div className="redact-zoom">
            <button
              type="button"
              className="btn btn-sm redact-zoom-step"
              onClick={() => changeZoom(ZOOM_STEPS[zoomStep - 1])}
              disabled={!canZoomOut}
              aria-label="Zoom out"
              title="Zoom out"
            >
              −
            </button>
            <span className="muted redact-zoom-level">{Math.round(zoom * 100)}%</span>
            <button
              type="button"
              className="btn btn-sm redact-zoom-step"
              onClick={() => changeZoom(ZOOM_STEPS[zoomStep + 1])}
              disabled={!canZoomIn}
              aria-label="Zoom in"
              title="Zoom in"
            >
              +
            </button>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => changeZoom(DEFAULT_ZOOM)}
              disabled={zoom === DEFAULT_ZOOM}
            >
              Reset zoom
            </button>
          </div>

          {pageCount > 1 && (
            <div className="redact-pager">
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => setPageIndex((index) => Math.max(0, index - 1))}
                disabled={pageIndex === 0}
              >
                Previous
              </button>
              <span className="muted">
                Page {pageIndex + 1} of {pageCount}
              </span>
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => setPageIndex((index) => Math.min(pageCount - 1, index + 1))}
                disabled={pageIndex === pageCount - 1}
              >
                Next
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * The list of figures the scan turned up, and the accept/reject decisions.
 *
 * It shows the current page's undecided suggestions, because that is what the
 * boxes on screen correspond to — deciding something you cannot see is how a
 * figure gets covered by accident. Anything waiting on another page is
 * counted, with a button to jump to it.
 *
 * The panel disappears once every suggestion has been decided: at that point
 * it has nothing left to say, and the page is the only thing worth looking at.
 */
function SuggestionReview({
  scanning,
  scanError,
  scan,
  pending,
  pendingHere,
  pendingElsewhere,
  open,
  onToggle,
  selectedId,
  onSelect,
  onAccept,
  onReject,
  onAcceptAll,
  onRejectAll,
  onGoToPending,
  pageCount,
  busy,
}) {
  if (scanning) {
    return (
      <div className="redact-review">
        <p className="redact-review-note muted">Scanning the document for financial figures…</p>
      </div>
    );
  }

  // A failed scan is worth saying out loud — "no suggestions" and "the scan
  // did not run" look identical otherwise, and only one of them means the
  // page can be trusted to be clean.
  if (scanError) {
    return (
      <div className="redact-review">
        <p className="redact-review-note muted">
          Automatic detection did not run: {scanError} Mark the areas by hand.
        </p>
      </div>
    );
  }

  if (!scan) return null;

  // Nothing left to decide. The scan's own message still shows when it had
  // something to report about what it could not read — a page it could not
  // reach is worth knowing about long after the list is empty.
  if (pending.length === 0) {
    if (!scan.message) return null;
    return (
      <div className="redact-review">
        <p className="redact-review-note muted">{scan.message}</p>
      </div>
    );
  }

  return (
    <div className="redact-review">
      <div className="redact-review-head">
        <div className="redact-review-title">
          <strong>{pending.length}</strong>
          <span className="muted">
            {pending.length === 1 ? " suggested area" : " suggested areas"} to review
            {pageCount > 1 && ` — ${pendingHere.length} on this page`}
          </span>
        </div>
        <div className="redact-review-actions">
          {pendingElsewhere > 0 && (
            <button type="button" className="btn btn-sm" onClick={onGoToPending} disabled={busy}>
              {pendingElsewhere} on other pages
            </button>
          )}
          <button type="button" className="btn btn-sm" onClick={onAcceptAll} disabled={busy}>
            Accept all
          </button>
          <button type="button" className="btn btn-sm" onClick={onRejectAll} disabled={busy}>
            Reject all
          </button>
          <button
            type="button"
            className="btn btn-sm"
            onClick={onToggle}
            aria-expanded={open}
            disabled={busy}
          >
            {open ? "Hide list" : "Show list"}
          </button>
        </div>
      </div>

      {scan.message && <p className="redact-review-note muted">{scan.message}</p>}

      {open && (
        <ul className="redact-review-list">
          {pendingHere.length === 0 ? (
            <li className="redact-review-empty muted">
              Nothing left to review on this page.
            </li>
          ) : (
            pendingHere.map((suggestion) => (
              <li
                key={suggestion.id}
                className={
                  suggestion.id === selectedId
                    ? "redact-review-item redact-review-item-selected"
                    : "redact-review-item"
                }
              >
                {/* The row itself highlights the box on the page, so a
                    suggestion can be located before it is judged. */}
                <button
                  type="button"
                  className="redact-review-pick"
                  onClick={() => onSelect(suggestion.id)}
                  title="Highlight this area on the page"
                >
                  <span className="redact-review-label">{suggestion.label}</span>
                  <span className="redact-review-text">{suggestion.text}</span>
                  {suggestion.source === "ocr" && (
                    <span
                      className="redact-review-flag redact-review-flag-ocr"
                      title="Read by OCR from a scanned page — check it against the page"
                    >
                      OCR
                    </span>
                  )}
                  {suggestion.confidence === "medium" && (
                    <span className="redact-review-flag">unsure</span>
                  )}
                </button>
                <span className="redact-review-decide">
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => onAccept(suggestion.id)}
                    disabled={busy}
                    title="Redact this area"
                  >
                    Accept
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => onReject(suggestion.id)}
                    disabled={busy}
                    title="Leave this area alone"
                  >
                    Reject
                  </button>
                </span>
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  );
}
