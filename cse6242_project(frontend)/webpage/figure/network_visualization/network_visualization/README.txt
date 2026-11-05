Scholar Compass – Research Collaboration Graph
==============================================

An interactive academic collaboration visualization powered by D3.js (v5).
This module reveals how researchers connect through co-authored papers,
highlighting collaboration strength, activity level, and the central advisor
or main researcher within a scholarly community.


------------------------------------------------------------
1. Features
------------------------------------------------------------
- Force-directed, interactive collaboration graph
- Time-decayed collaboration weighting (recent papers weigh more)
- Node color and size encoding based on unique collaborators
- Hoverable tooltips with paper-level details
- Draggable layout for manual repositioning
- Automatic aggregation of multiple papers per author pair
- 45° label placement for improved readability
- Distinct visual highlighting for the main researcher


------------------------------------------------------------
2. Project Structure
------------------------------------------------------------
network.html     – Main visualization (HTML + JS + CSS)
data.csv         – Collaboration dataset (see format below)


------------------------------------------------------------
3. Data Format
------------------------------------------------------------
Each row in data.csv represents one collaboration record:

index,author1,author2,year,citation

Columns:
index     – Row index
author1   – Primary author’s name
author2   – Collaborator’s name
year      – Year of publication
citation  – Total citation count for the paper


------------------------------------------------------------
4. Data Processing Logic
------------------------------------------------------------
1. Weight Calculation
   Each paper contributes a time-decayed weight:
   weight = citation × exp( -lambda × (current calendar year - year) )
   where lambda = 0.1 (recent papers contribute more).

2. Aggregation
   All papers between the same two authors are summed to compute total edge weight.

3. Node Degree
   Each researcher’s degree = number of unique collaborators.
   (One collaborator counts once, regardless of multiple co-authored papers.)


------------------------------------------------------------
5. Visualization Design
------------------------------------------------------------
Node Encoding:
- Color:
  - Blue (#2c7fb8): researcher with ≥ 2 collaborators
  - Yellow (#edf8b1): researcher with < 2 collaborators
- Size: linearly scaled by degree (range 5–20 px)

Edge Encoding:
- Gray solid line: strong collaboration (weight ≥ median)
- Green dashed line: weak collaboration (weight < median)

Label Position:
- Placed 45° above the node center, at distance (r + 8)

Main Researcher Highlight:
- The designated main researcher (e.g., advisor) is rendered differently:
  - Shape: five-pointed star (or double-layer circle variant)
  - Color: gold (#FFD700)
  - Larger size and bold label for immediate visual emphasis


------------------------------------------------------------
6. Interaction
------------------------------------------------------------
- Drag nodes to manually rearrange layout.
- Hover over edges to view detailed paper information:
  year, citation count, decay factor, individual weight.
- Force simulation dynamically updates node positions.


------------------------------------------------------------
7. Key Parameters
------------------------------------------------------------
lambda = 0.1                         Time decay factor
forceManyBody().strength(-250)       Node repulsion strength
forceLink().distance(150)            Ideal link length
sizeScale: [5, 20]                   Node radius range
offset = 8                           Label distance from node center


------------------------------------------------------------
8. How to Run
------------------------------------------------------------
1. Place network.html and data.csv in the same directory.
2. From this directory run `python3 -m http.server 8000`, then open `http://localhost:8000/network.html`.
3. Ensure Internet access to load D3.js:
   https://d3js.org/d3.v5.min.js
4. The interactive collaboration graph will render automatically. The bundled CSV is a historical Kai Wang example, not live data.


------------------------------------------------------------
9. Example Output
------------------------------------------------------------
The visualization illustrates an academic collaboration network where:
- Larger, blue nodes = well-connected researchers.
- Smaller, yellow nodes = isolated or less active collaborators.
- Gray solid lines = strong, active collaborations.
- Green dashed lines = weaker or older collaborations.
- Gold star = the main researcher or advisor.


------------------------------------------------------------
10. Summary
------------------------------------------------------------
This visualization integrates:
- Quantitative metrics (citations, recency, collaboration diversity)
- Intuitive visual encoding (color, size, shape)
- Interactive analysis for exploring academic relationships

As the visual core of "Scholar Compass",
it provides an intuitive overview of an advisor’s academic network,
collaboration patterns, and evolving research influence.


------------------------------------------------------------
11. Author
------------------------------------------------------------
Developed by: hjiang401
Module: Scholar Compass – Research Visualization
Language: JavaScript (D3.js v5)
Date: 2025
