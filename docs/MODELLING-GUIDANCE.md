# What the CIM guidelines say about building the model, and what MMP does about it

Read from the CIM *Estimation of Mineral Resources and Mineral Reserves Best
Practice Guidelines* (29 November 2019) and the CIM *Definition Standards*
(10 May 2014), for modelling method only.

- <https://mrmr.cim.org/media/1113/cim-estimation-of-mineral-resources-nov-29-19.pdf>
- <https://mrmr.cim.org/media/1128/cim-definition-standards_2014.pdf>

The guidelines are deliberately light on numbers — they say what must be justified,
not what the number should be. That is useful in itself: it means the defensible
choices are the ones derived from the deposit's own data, which is the direction MMP
was already moving.

## What they endorse that MMP already does

**Density from a formula on grade.** §4.3 and §6.5 allow density to be assigned per
domain, estimated from sample data, or taken from "a formula based on grade or
mineralogy" — with the condition that **any formula must be validated against
measured values**. MMP's style-and-grade density model is therefore a legitimate
approach, but the validation is not optional. `scripts/validate_density.py` scores
it against every bulk density a technical report in the corpus states.

**Classification from drill support.** §6.11 lists the numeric criteria used in
practice: number of data points, number of drill holes, **estimation pass**, kriging
variance, regression slope, distance relative to the variogram range, and distance
to drill holes. MMP's tiers are built from the estimation pass and the number of
holes, which is squarely among these. No numeric thresholds are given anywhere in
the guidelines, which is why MMP calibrates its own against filed statements rather
than asserting them.

**Block size justified by geometry.** §6.9 requires block size to follow
mineralization geometry, mining method and drill spacing. MMP derives it from the
deposit style and the measured intercept width.

## What they require that MMP was not doing

**Declustering (§6.4).** Used "to reduce clustering bias and obtain an unbiased
global mean", and specifically to support unbiased grade-tonnage curves. MMP's
grade-tonnage curve is the thing every accuracy score is measured on, and tightly
drilled ground was over-weighting it. Implemented in `minemodelingpro.decluster`.

**Composite statistics compared against raw assay statistics (§6.4).** A check that
compositing preserved the distribution. MMP composites un-assayed ground at zero
grade, which is exactly the kind of thing this check exists to catch — and it would
have caught the dilution the validation harness eventually found the hard way.

**Composites must not cross domain boundaries (§6.4).** Binding once MMP has a
domain, and a constraint on how the domain is built.

**Validation against a nearest-neighbour model (§6.10.1).** Global bias is checked
against nearest-neighbour or declustered composite statistics, and local bias with
swath plots. This matters more to MMP than to a normal estimate: it is a check that
works on a deposit with **no filed statement at all**, which is the case MMP exists
to serve. A model whose inverse-distance mean departs badly from its
nearest-neighbour mean is smoothing or diluting, and that can be said without any
external answer to compare against.

**Classification smoothing — the "spotted dog" (§6.11).** Numeric criteria alone
scatter isolated blocks of one category through another. The guidelines call numeric
criteria "only a first step" and point to classification wireframes or
"categorization smoothers".

## What they say about mining method, and why MMP splits the model

A Mineral Resource requires reasonable prospects for eventual economic extraction,
and the Definition Standards list the mining method among the assumptions behind it
— an open-pit configuration or a preferred underground method. Companies report
accordingly, usually as a pit-constrained tonnage and an underground tonnage at a
higher cut-off. A single undifferentiated tonnage is not comparable with either.
`minemodelingpro.mining_method` splits MMP's estimate the same way.

The Standards also note that "eventual" has a horizon: more than 50 years may be
reasonable for some bulk commodities, while 10 to 15 years is normally the limit for
many gold deposits.

## On terminology

MMP calls its output a resource estimate, because that is what it is: an estimate of
the resource implied by the public drilling. It is not a company's filed statement
and it was not prepared by a Qualified Person, and the model page says where each
number came from so the two are never confused. Where MMP shows a company's own
filed statement it reproduces that company's categories as filed and keeps them
separate from MMP's own tiers, which describe drill support and carry their own
names.
