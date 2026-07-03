# Adaptive Visual Query Strategies

Use 3-8 plain English words and one filmable visual idea per query. Avoid long keyword dumps: Pexels and Pixabay treat each request as one search string, not as a batch of independent searches.

## Four planned options

- `direct`: concrete subject, place, action, object, or event stated in the narration.
- `comprehensive`: one broader umbrella phrase likely to return a mixture of literal, contextual, and symbolic results. Keep the central theme but omit unnecessary proper nouns and details.
- `associative`: a parallel situation, relationship, social behavior, or context that conveys the same idea without repeating the sentence's nouns.
- `metaphorical`: a concrete symbol for the sentence's emotion, structure, pressure, division, growth, isolation, repetition, or conflict.

Example for “双方拿出截图、数据和长篇大论争论”:

- direct: `people arguing phone screenshots`
- comprehensive: `online debate conflicting information`
- associative: `crowd debating checking smartphones`
- metaphorical: `opposing arrows tangled information`

The four options are planned in advance. Stop executing later options once a point has three candidates scoring 55+ and at least one candidate scoring 75+. If all three are only reserve-quality, continue broadening in search of one strong match. After round 6, retain whatever scores 55+ even when the ideal stop condition was not reached.

## Round 6 rewrite

Review `previous_queries` and `rejected_or_weak_candidates`. Write one substantially different query, not a cosmetic synonym swap. Choose `visual_intent` from `direct`, `mixed`, `associative`, or `metaphorical` according to the most promising remaining route.

```json
{
  "point_id": "001",
  "visual_intent": "associative",
  "query": "crowd debating checking smartphones",
  "reason": "The social behavior conveys competing interpretations without repeating the failed literal query."
}
```

Reject random sunsets, unrelated drone footage, and generic walking shots unless the narration specifically calls for that mood. Complete every listed point without changing IDs before running round 6.
