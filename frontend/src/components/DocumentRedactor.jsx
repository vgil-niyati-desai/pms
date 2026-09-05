import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import FilePreview from "./FilePreview";
import {
  createRedactedCopy,
  getRedactionSource,
  redactionPageImageUrl,
} from "../api/documents";

/**
 * Manual redaction: draw boxes over the sensitive parts of a document, check
 * the result, then download a permanently redacted copy of it.
 *
 * Three things are worth knowing about how this works.
 *
 * The page is an image, not the usual preview. The normal preview hands a PDF
 * to the browser's own viewer inside an <iframe>, and an iframe is opaque —
 * nothing outside it can tell which page a click landed on, or where. So
 * redaction asks the backend to render each page and draws over that instead,
 * which also means PDFs and PNG/JPGs are selected in exactly the same way.
 *
 * The boxes here are only a *selection*. They are drawn in fractions of the
 * page (origin top-left), sent to the backend, and it is the backend that
 * deletes the covered text from the copy it generates. Nothing on this screen
 * is what makes the copy safe, and nothing on this screen changes the
 * original — it is still there, unredacted, behind Close.
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

  const pages = source?.pages ?? [];
  const page = pages[pageIndex];
  const pageAreas = areas.filter((area) => area.page === pageIndex);

  // A new page is a new image to wait for, and the message about the last
  // generated copy no longer describes what is on screen.
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

  // Changing the selection makes any built copy stale: it no longer shows
  // what would download. Dropping both here means the two can never disagree,
  // whichever action changed the areas.
  useEffect(() => {
    builtRef.current = null;
    showPreview(null);
  }, [areas, showPreview]);

  // Nothing else revokes the last object URL when the redactor unmounts.
  // Done through the ref rather than showPreview, which would also be setting
  // state on a component that is going away.
  useEffect(
    () => () => {
      if (previewRef.current) URL.revokeObjectURL(previewRef.current.url);
    },
    [],
  );

  const removeSelected = useCallback(() => {
    if (selectedId === null) return;
    setAreas((current) => current.filter((area) => area.id !== selectedId));
    setSelectedId(null);
    setDone("");
  }, [selectedId]);

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
    const id = nextIdRef.current++;
    setAreas((current) => [...current, { id, page: pageIndex, ...rect }]);
    setSelectedId(id);
  }

  function cancelDraw() {
    dragOriginRef.current = null;
    panOriginRef.current = null;
    setPanning(false);
    setDraft(null);
  }

  function clearAll() {
    setAreas([]);
    setSelectedId(null);
    setDone("");
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
    const payload = areas.map(({ page: index, x, y, width, height }) => ({
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

  return (
    <div className="redact">
      <div className="redact-bar">
        <div className="redact-bar-info">
          {preview ? (
            <span className="muted">
              Previewing the redacted copy — <strong>{areas.length}</strong>
              {areas.length === 1 ? " area applied" : " areas applied"}
            </span>
          ) : (
            <>
              <strong>{areas.length}</strong>
              <span className="muted">
                {areas.length === 1 ? " area marked" : " areas marked"}
                {pageCount > 1 && areas.length > 0 && ` (${pageAreas.length} on this page)`}
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
                Remove selection
              </button>
              <button
                type="button"
                className="btn btn-sm"
                onClick={clearAll}
                disabled={areas.length === 0 || busy !== ""}
              >
                Clear all
              </button>
              <button
                type="button"
                className="btn btn-sm"
                onClick={previewRedaction}
                disabled={areas.length === 0 || busy !== ""}
              >
                {busy === "preview" ? "Preparing…" : "Preview redaction"}
              </button>
            </>
          )}
          <button
            type="button"
            className="btn btn-sm btn-primary"
            onClick={generate}
            disabled={areas.length === 0 || busy !== ""}
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
            Drag across the costs or amounts to cover. Click a box to see what is under it,
            then Remove selection or press Delete. Preview redaction shows the finished copy
            before you download it — the original entry is not changed.
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
            {pageAreas.map((area) => (
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
