"""GraphQL documents — exact query strings, no logic.

The five patient-API prefetch queries plus the provider-API fallback. Kept
verbatim so they're easy to diff against the Photon schema / captured traffic.
"""

# (1) order context
ORDER = """
query GetOrder($id: ID!) {
  order(id: $id) {
    id state isReroutable readyBy readyByDay readyByTime
    metadata { type marketIsRequired fulfillmentMethod
      routingHistory { pharmacy { id name } selector reason createdAt } }
    organization { name settings { patientUx {
      enablePatientRerouting enablePatientDeliveryPharmacies patientFeaturedPharmacyName } } }
    pharmacy { id name phone address { street1 city state } fulfillmentTypes }
    fulfillment { type state }
    fills { prescription { id dispenseQuantity dispenseUnit instructions }
            treatment { id name } }
    exceptions { __typename }
  }
}
"""

# (2) marketplace offers — result array MAY BE EMPTY
OFFER_BUNDLES = """
query GetOfferBundlesForOrder($orderId: ID!) {
  offerBundles(orderId: $orderId) {
    source isPromoted attributeTags { kind label }
    pharmacy { id name }
    offers { prescription { treatment { name } }
             prescriptionPrice { amount retailAmount }
             deliveryEstimate { deliveryPromise } }
  }
}
"""

# (3) mail-order list
PHARMACIES = """
query GetPharmacies {
  pharmacies(fulfillmentType: MAIL_ORDER, integrated: false, limit: 50) { id name }
}
"""

# (4) org FAQs
FAQS = """
query GetFaqs($orderId: ID!) {
  faqs(orderId: $orderId) { question answer }
}
"""

# (5) nearby pickup options with discount pricing (GoodRx-sourced)
PHARMACIES_BY_LOCATION = """
query GetPharmaciesByLocation($location: LatLongSearch!, $limit: Int, $includePrice: Boolean,
                              $name: String, $is24hr: Boolean, $offset: Int) {
  pharmaciesByLocation(location: $location, limit: $limit, includePrice: $includePrice,
                       name: $name, is24hr: $is24hr, offset: $offset) {
    id name distance isOpen address { street1 city }
    nextEvents { open { ... on PharmacyOpenEvent { type datetime }
                        ... on PharmacyCloseEvent { type datetime }
                        ... on PharmacyOpen24HrEvent { type } }
                 close { ... on PharmacyOpenEvent { type datetime }
                         ... on PharmacyCloseEvent { type datetime }
                         ... on PharmacyOpen24HrEvent { type } } }
    price retailPrice source
  }
}
"""

# Inbound: find a caller's latest order by phone (Clinical API, M2M token).
# patientPhone must be E.164 (+1XXXXXXXXXX); bare digits return nothing.
ORDERS_BY_PHONE = """
query OrdersByPhone($phone: String!) {
  orders(pageSize: 1, sortBy: {option: ORDER_CREATED_AT, direction: DESC},
         filter: {patientPhone: $phone}) {
    id patient { id dateOfBirth }
  }
}
"""

# In-call re-read after a mutation (did the change land?).
ORDER_STATUS = """
query GetOrderStatus($id: ID!) {
  order(id: $id) {
    id state isReroutable readyBy readyByDay readyByTime
    pharmacy { id name }
    fulfillment { type state }
  }
}
"""

# --- Patient-API mutations (all return Boolean!) ---
REROUTE_ORDER = """
mutation RerouteOrder($orderId: ID!, $pharmacyId: ID!, $patientSelectedPrice: Boolean, $reason: String) {
  rerouteOrder(orderId: $orderId, pharmacyId: $pharmacyId,
               patientSelectedPrice: $patientSelectedPrice, reason: $reason)
}
"""

SET_ORDER_PHARMACY = """
mutation SetOrderPharmacy($orderId: ID!, $pharmacyId: ID!, $readyByDay: String,
                          $readyByTime: DateTime, $patientSelectedPrice: Boolean) {
  setOrderPharmacy(orderId: $orderId, pharmacyId: $pharmacyId, readyByDay: $readyByDay,
                   readyByTime: $readyByTime, patientSelectedPrice: $patientSelectedPrice)
}
"""

SET_PREFERRED_PHARMACY = """
mutation SetPreferredPharmacy($patientId: String, $pharmacyId: String) {
  setPreferredPharmacy(patientId: $patientId, pharmacyId: $pharmacyId)
}
"""

# Fallback: minimal order context from the provider API (Bearer).
PROVIDER_FALLBACK = """
query($id: ID!) {
  order(id: $id) {
    id state
    pharmacy { id name address { street1 city } }
    fills { treatment { name } prescription { id } }
  }
}
"""
