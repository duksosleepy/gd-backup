import React, { createContext, useContext, useEffect, useReducer } from 'react'

import { getCurrentUser, logout } from '@/repository'

type User = {
  email: string
  name: string
} | null

type AuthState = {
  isAuthenticated: boolean
  isAuthenticationEnabled: boolean
  isLoading: boolean
  logout: () => Promise<any>
  user: User
}

type Action =
  | { type: 'LOGIN'; payload: { user: User; isAuthenticationEnabled: boolean } }
  | { type: 'DISABLE_AUTH' }
  | { type: 'POPULATE'; payload: User }
  | { type: 'LOGOUT' }
  | { type: 'STOP_LOADING' }

const initialState: AuthState = {
  isAuthenticated: true,
  isAuthenticationEnabled: false,
  isLoading: false,
  logout: async () => {},
  user: null,
}

const StateContext = createContext<AuthState>(initialState)

const reducer = (state: AuthState, action: Action): AuthState => {
  switch (action.type) {
    case 'LOGIN':
      return {
        ...state,
        isAuthenticated: true,
        isAuthenticationEnabled: action.payload.isAuthenticationEnabled,
        user: action.payload.user,
      }
    case 'DISABLE_AUTH':
      return {
        ...state,
        isAuthenticated: true,
        isAuthenticationEnabled: false,
        user: null,
      }
    case 'LOGOUT':
      return {
        ...state,
        isAuthenticated: false,
        user: null,
      }
    case 'POPULATE':
      return {
        ...state,
        user: action.payload
          ? {
              ...state.user,
              ...action.payload,
            }
          : null,
      }
    case 'STOP_LOADING':
      return {
        ...state,
        isLoading: false,
      }
    default:
      throw new Error('Unknown action type')
  }
}

export const AuthProvider = ({ children }: { children: React.ReactNode }) => {
  const [state, dispatch] = useReducer(reducer, initialState)

  useEffect(() => {
    const loadUser = async () => {
      try {
        const { is_authentication_enabled, user } = await getCurrentUser()

        if (is_authentication_enabled && user) {
          dispatch({
            type: 'LOGIN',
            payload: {
              user,
              isAuthenticationEnabled: true,
            },
          })
        } else {
          dispatch({ type: 'DISABLE_AUTH' })
        }
      } catch {
        dispatch({ type: 'DISABLE_AUTH' })
      } finally {
        dispatch({ type: 'STOP_LOADING' })
      }
    }

    loadUser()
  }, [])

  return (
    <StateContext.Provider value={state}>
      {children}
    </StateContext.Provider>
  )
}

export const useAuthState = () => useContext(StateContext)
