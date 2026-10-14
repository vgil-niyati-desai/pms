import Skeleton from "./Skeleton";

/**
 * A record screen's shape while the record is loading: the identity block and
 * the tab strip under it, in the same card and the same grid the real header
 * uses, so the one thing that changes when the record arrives is the text.
 *
 * `facts` and `tabs` are how many of each this screen has -- four facts and
 * two tabs on a project, six and three on an employee -- so the placeholder is
 * the height of the header that replaces it rather than a generic one.
 *
 * Hidden from screen readers, which are told "Loading..." once instead.
 */
export default function DetailSkeleton({ facts = 4, tabs = 2 }) {
  return (
    <>
      <p className="sr-only" role="status">
        Loading...
      </p>
      <div className="detail-skeleton" aria-hidden="true">
        <header className="detail-header card">
          <div className="detail-header-top">
            {/* The title and the line under it. Flexed to take the room the
                actions do not, since there is no text here to size it. */}
            <div className="detail-skeleton-identity">
              <Skeleton width="48%" height={22} />
              <Skeleton width="26%" height={14} />
            </div>
            <div className="detail-actions">
              <Skeleton width={92} height={34} />
              <Skeleton width={132} height={34} />
            </div>
          </div>

          <dl className="detail-facts">
            {Array.from({ length: facts }, (_, index) => (
              <div key={index}>
                <dt>
                  <Skeleton width="58%" height={9} />
                </dt>
                <dd>
                  <Skeleton width="82%" height={14} />
                </dd>
              </div>
            ))}
          </dl>
        </header>

        <div className="tabs">
          {Array.from({ length: tabs }, (_, index) => (
            <span className="tab" key={index}>
              <Skeleton width={68} height={13} />
            </span>
          ))}
        </div>
      </div>
    </>
  );
}
