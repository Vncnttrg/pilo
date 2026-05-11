export interface Listing {
  id: number
  title: string
  brand: string
  price: number
  currency: string
  favourites: number
  image_url: string
  image_urls: string[]
  url: string
  style_score: number
  price_score: number
  fav_score: number
  deal_score: number
  final_score: number
}

export type Action = 'like' | 'skip' | 'save' | 'golden'
