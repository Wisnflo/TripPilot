// A failed or cancelled attempt must not replace a user's usable itinerary.
export function selectOutcome(previous,previousId,incoming,incomingId){
 const retained=Boolean(previousId&&previous?.plan&&!incoming.plan&&incoming.status!=='needs_clarification');
 return retained?{current:{...previous,lastAttempt:incoming},active:previousId,retained:true}:{current:incoming,active:incomingId,retained:false};
}
