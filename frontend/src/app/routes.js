/**
 * Route paths in one place, so screens link to each other through these
 * helpers instead of hardcoded strings.
 */
export const paths = {
  home: () => "/",

  projects: () => "/projects",
  newProject: () => "/projects/new",
  project: (id) => `/projects/${id}`,
  editProject: (id) => `/projects/${id}/edit`,

  cvs: () => "/cvs",
  newEmployee: () => "/cvs/new",
  employee: (id) => `/cvs/${id}`,
  editEmployee: (id) => `/cvs/${id}/edit`,

  tenders: () => "/tenders",
  newTender: () => "/tenders/new",
  tender: (id) => `/tenders/${id}`,
  editTender: (id) => `/tenders/${id}/edit`,

  // The original single-screen document log, now the fourth area in the
  // sidebar rather than a screen reached only by URL.
  documents: () => "/documents",
};

/** The four areas, in sidebar order. */
export const AREAS = [
  {
    key: "projects",
    label: "Projects",
    path: paths.projects(),
    action: { label: "+ New Project", path: paths.newProject(), available: true },
  },
  {
    key: "cvs",
    label: "CVs",
    path: paths.cvs(),
    action: { label: "+ New Employee", path: paths.newEmployee(), available: true },
  },
  {
    key: "tenders",
    label: "Tenders",
    path: paths.tenders(),
    action: { label: "+ New Tender", path: paths.newTender(), available: true },
  },
  // No action: the document log captures a new entry through the form on the
  // page itself, so there is no separate route for the topbar button to go
  // to. AppShell renders the button only for an area that has one.
  {
    key: "documents",
    label: "Documents",
    path: paths.documents(),
  },
];

/** Titles for routes that have no sidebar entry of their own. */
const EXTRA_SECTIONS = [{ path: paths.documents(), label: "Documents" }];

/**
 * The section a path belongs to: one of AREAS, an extra section, or null on
 * a route that belongs to neither (Not Found).
 */
export function sectionForPath(pathname) {
  const isUnder = (base) => pathname === base || pathname.startsWith(`${base}/`);
  return (
    AREAS.find((area) => isUnder(area.path)) ||
    EXTRA_SECTIONS.find((section) => isUnder(section.path)) ||
    null
  );
}
