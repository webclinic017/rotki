import i18n from '@/i18n';
import { ActionDataEntry } from '@/store/const';
import { KrakenStakingEventType } from '@/types/staking';

export const ADEX_HISTORY = 'adexHistory' as const;
export const ADEX_BALANCES = 'adexBalances' as const;
export const ETH2_DETAILS = 'eth2Details' as const;
export const ETH2_DEPOSITS = 'eth2Deposits' as const;
export const ETH2_DAILY_STATS = 'eth2DailyStats' as const;
export const RESET = 'reset' as const;

export const ACTION_PURGE_DATA = 'purgeData' as const;

export const krakenStakingEventTypeData: ActionDataEntry[] = [
  {
    identifier: KrakenStakingEventType.REWARD,
    label: i18n.t('kraken_staking_events.types.staking_reward').toString()
  },
  {
    identifier: KrakenStakingEventType.RECEIVE_WRAPPED,
    label: i18n.t('kraken_staking_events.types.receive_staked_asset').toString()
  },
  {
    identifier: KrakenStakingEventType.DEPOSIT_ASSET,
    label: i18n.t('kraken_staking_events.types.stake_asset').toString()
  },
  {
    identifier: KrakenStakingEventType.REMOVE_ASSET,
    label: i18n.t('kraken_staking_events.types.unstake_asset').toString()
  }
];
