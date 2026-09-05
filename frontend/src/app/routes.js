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

  // The original single-screen document log. Not one of the three areas, so
  // it is reachable by URL and from the Projects screen rather than the
  // sidebar, and stays working until Projects replaces it.
  documents: () => "/documents",
};

/** The three areas, in sidebar order. */
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
